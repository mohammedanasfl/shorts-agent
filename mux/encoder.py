"""ffmpeg driver for the mux stage -- the assembly-stage analog of
video/browser.py. Two jobs: build one per-scene clip (trim video to the
voiceover length, mux the audio, overlay the karaoke caption PNGs), and
concatenate the finished scene clips into the final short. All geometry/encode
settings come from mux/config.py so every scene clip shares identical
parameters and the concat is a lossless stream copy."""
import json
import subprocess
from pathlib import Path

from common.log import log
from mux.config import (
    A_BITRATE,
    A_CHANNELS,
    A_CODEC,
    A_RATE,
    FPS,
    HEIGHT,
    PIX_FMT,
    V_CODEC,
    V_CRF,
    V_PRESET,
    WIDTH,
)


class MuxError(RuntimeError):
    """Raised when ffmpeg/ffprobe could not assemble a usable clip -- caught by
    mux/nodes.py's bounded retry loop."""


def probe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return float(out.stdout.strip())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as e:
        raise MuxError(f"ffprobe failed on {path}: {e}") from e


def _run(cmd: list[str], what: str) -> None:
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=True)
    except subprocess.CalledProcessError as e:
        # ffmpeg's real diagnostics are on stderr; surface the tail of it.
        tail = (e.stderr or "").strip().splitlines()[-4:]
        raise MuxError(f"{what} failed: {' / '.join(tail)}") from e
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise MuxError(f"{what} failed: {e}") from e


def _esc(t: float) -> str:
    # Commas inside overlay's enable=between(t,a,b) must be escaped so ffmpeg's
    # filtergraph parser doesn't read them as filter separators.
    return f"{t:.3f}"


def render_scene_clip(
    scene_id: str, video_path: Path, audio_path: Path, frames: list[dict],
    scene_dur: float, video_dur: float, dest: Path,
) -> None:
    """Assembles one scene: video (normalized to WIDTHxHEIGHT@FPS, trimmed to
    the voiceover) + the voiceover + each caption PNG overlaid during its word
    window. If the voiceover is longer than the clip (shouldn't happen with our
    assets, but guarded), the last video frame is held to cover the gap."""
    inputs = ["-i", str(video_path), "-i", str(audio_path)]
    for f in frames:
        inputs += ["-i", f["path"]]

    # Base: normalize geometry/fps. Hold the last frame if audio outlasts video.
    base = f"[0:v]scale={WIDTH}:{HEIGHT},setsar=1,fps={FPS}"
    if scene_dur > video_dur + 0.05:
        base += f",tpad=stop_mode=clone:stop_duration={scene_dur - video_dur:.3f}"
    base += "[v0]"

    graph = [base]
    label = "v0"
    for k, f in enumerate(frames):
        nxt = f"v{k + 1}"
        # PNG inputs start at ffmpeg input index 2 (0=video, 1=audio).
        graph.append(
            f"[{label}][{k + 2}:v]overlay=0:0:"
            f"enable='between(t\\,{_esc(f['start'])}\\,{_esc(f['end'])})'[{nxt}]"
        )
        label = nxt
    graph.append(f"[{label}]format={PIX_FMT}[vout]")

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(graph),
        "-map", "[vout]", "-map", "1:a",
        "-t", f"{scene_dur:.3f}", "-r", str(FPS),
        "-c:v", V_CODEC, "-preset", V_PRESET, "-crf", V_CRF, "-pix_fmt", PIX_FMT,
        "-c:a", A_CODEC, "-b:a", A_BITRATE, "-ar", str(A_RATE), "-ac", str(A_CHANNELS),
        "-movflags", "+faststart", str(dest),
    ]
    _run(cmd, f"{scene_id}: scene mux")
    log("mux", f"{scene_id}: scene clip -> {dest}")


def concat_clips(paths: list[Path], dest: Path, list_file: Path) -> None:
    """Concatenates finished scene clips (all identical params) into the final
    short via the concat demuxer with a lossless stream copy."""
    lines = "".join(f"file '{p.resolve()}'\n" for p in paths)
    list_file.write_text(lines)
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", "-movflags", "+faststart", str(dest),
    ]
    _run(cmd, "final concat")
    log("mux", f"concat -> {dest}")


def validate_video(path: Path) -> None:
    """Confirms ffmpeg produced something with a real video stream, so a later
    cache hit can never trust a truncated/failed encode."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise MuxError(f"ffprobe validation failed on {path}: {e}") from e
    if not json.loads(out.stdout).get("streams"):
        raise MuxError(f"{path} has no video stream")
