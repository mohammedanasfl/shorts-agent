"""Groq Whisper STT driver -- transcribes each scene's already-generated
voiceover into word-level timestamps, the data a future verify/mux stage needs
to burn timed captions into the final video. Holds a lazy module-level Groq
client singleton, same reasoning as audio/tts.py's TTS client (an API client
doesn't serialize for LangGraph's checkpointer)."""
from pathlib import Path

from groq import Groq

from caption.config import RATELIMIT_BASE_DELAY, STT_MODEL
from common.log import log
from common.retry import call_with_retry

_client = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq()
    return _client


class CaptionGenerationError(RuntimeError):
    """Raised when a scene's audio could not be transcribed into word-level
    timestamps -- caught by caption/nodes.py's bounded retry loop."""


def transcribe_scene(scene_id: str, audio_path: str) -> list[dict]:
    """Returns [{"word": str, "start": float, "end": float}, ...] for one
    scene's audio clip."""
    client = _get_client()
    path = Path(audio_path)

    try:
        with path.open("rb") as f:
            response = call_with_retry(
                lambda: client.audio.transcriptions.create(
                    model=STT_MODEL,
                    file=f,
                    response_format="verbose_json",
                    timestamp_granularities=["word"],
                ),
                base_delay=RATELIMIT_BASE_DELAY,
            )
    except Exception as e:
        raise CaptionGenerationError(f"STT request failed for {scene_id}: {e}") from e

    words = getattr(response, "words", None) or []
    parsed = [
        {"word": w["word"], "start": w["start"], "end": w["end"]} if isinstance(w, dict)
        else {"word": w.word, "start": w.start, "end": w.end}
        for w in words
    ]
    if not parsed:
        raise CaptionGenerationError(f"{scene_id}: transcription returned zero words")

    log("caption", f"{scene_id}: done -> {len(parsed)} word(s)")
    return parsed
