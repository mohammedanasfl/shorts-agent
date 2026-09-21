"""Groq Orpheus TTS driver -- the audio-stage analog of video/browser.py's
Playwright driver. Holds a lazy module-level Groq client singleton, kept
outside graph state (an API client doesn't serialize for LangGraph's
checkpointer, same reasoning as video's browser singleton)."""
import re
import wave
from pathlib import Path

from groq import Groq

from audio.config import MAX_INPUT_CHARS, OUTPUT_DIR, RATELIMIT_BASE_DELAY, RESPONSE_FORMAT, TTS_MODEL
from common.log import log
from common.retry import call_with_retry

_client = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq()
    return _client


class AudioGenerationError(RuntimeError):
    """Raised when a scene's narration could not be turned into a usable
    audio clip -- caught by audio/nodes.py's bounded retry loop."""


def _chunk(text: str, max_chars: int = MAX_INPUT_CHARS) -> list[str]:
    """Splits text into <=max_chars pieces on sentence boundaries first, then
    word boundaries, so Orpheus's per-request input cap never truncates
    mid-word. Our narration lines run well under this cap, so the common
    case is a single chunk; this only kicks in for the rare long line."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        candidate = f"{current} {sentence}".strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(sentence) <= max_chars:
            current = sentence
        else:
            current = ""
            for word in sentence.split():
                candidate = f"{current} {word}".strip()
                if len(candidate) <= max_chars:
                    current = candidate
                else:
                    chunks.append(current)
                    current = word
    if current:
        chunks.append(current)
    return chunks


def _concat_wavs(paths: list[Path], dest: Path) -> None:
    """Also used for the single-chunk case (see generate_audio) to normalize
    Groq's response: Orpheus streams WAV with a placeholder RIFF/data size of
    0xFFFFFFFF ("unknown length, read to EOF") instead of the real byte count.
    Python's wave.Wave_read reports that placeholder verbatim as a bogus
    getnframes(), so we deliberately read nchannels/sampwidth/framerate only
    (never setparams()/setnframes(), which would carry the bogus count into
    the header we write) and let Wave_write compute the real size from actual
    bytes written on close()."""
    with wave.open(str(paths[0]), "rb") as first:
        nchannels, sampwidth, framerate = first.getnchannels(), first.getsampwidth(), first.getframerate()
        frames = [first.readframes(first.getnframes())]
    for p in paths[1:]:
        with wave.open(str(p), "rb") as w:
            frames.append(w.readframes(w.getnframes()))
    with wave.open(str(dest), "wb") as out:
        out.setnchannels(nchannels)
        out.setsampwidth(sampwidth)
        out.setframerate(framerate)
        for f in frames:
            out.writeframes(f)


def _validate(path: Path) -> None:
    try:
        with wave.open(str(path), "rb") as w:
            if w.getnframes() <= 0:
                raise AudioGenerationError(f"{path} has zero audio frames")
    except wave.Error as e:
        raise AudioGenerationError(f"{path} is not a valid WAV file: {e}") from e


def generate_audio(scene_id: str, tts_input: str, voice: str) -> Path:
    """Generates one scene's voiceover and downloads it to OUTPUT_DIR. Mirrors
    video/browser.py's generate_video: builds its own dest path, writes the
    cache sidecar only after validation passes, cleans up temp chunk files
    whether it succeeds or fails."""
    chunks = _chunk(tts_input)
    client = _get_client()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUTPUT_DIR / f"{scene_id}.wav"
    tmp_paths = [dest.with_suffix(f".part{i}.wav") for i in range(len(chunks))]

    try:
        for chunk, tmp_path in zip(chunks, tmp_paths):
            try:
                response = call_with_retry(
                    lambda c=chunk: client.audio.speech.create(
                        model=TTS_MODEL, voice=voice, input=c, response_format=RESPONSE_FORMAT
                    ),
                    base_delay=RATELIMIT_BASE_DELAY,
                )
            except Exception as e:
                raise AudioGenerationError(f"TTS request failed for {scene_id}: {e}") from e
            response.write_to_file(tmp_path)

        # Always normalize through _concat_wavs, even for a single chunk --
        # a raw rename would ship Groq's placeholder RIFF size verbatim.
        _concat_wavs(tmp_paths, dest)
        _validate(dest)

        cache_key = f"{voice}|{tts_input}"
        dest.with_suffix(".prompt.txt").write_text(cache_key)
        log("audio", f"{scene_id}: done -> {dest}")
        return dest
    finally:
        for p in tmp_paths:
            if p.exists():
                p.unlink()
