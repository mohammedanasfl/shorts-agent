"""Node logic for the mux (final-assembly) graph. See mux/agent.py for wiring.

Reads each scene's video from output/videos/ and voiceover from output/audio/
by scene_id (the project-wide on-disk convention), takes word timings straight
from the CaptionPackage it's handed, and produces one clip per scene before
concatenating them into the final short. A scene missing its video or audio is
skipped with a caveat rather than aborting the whole assembly."""
import hashlib
import json
import re
import shutil
from pathlib import Path

from caption.models import CaptionPackage

from common.log import log
from mux.captions import render_scene_captions
from mux.config import (
    A_BITRATE, A_CHANNELS, A_CODEC, A_RATE, AUDIO_DIR, CAPTION_STYLE_VERSION,
    FPS, HEIGHT, MAX_RETRIES_PER_SCENE, OUTPUT_DIR, PIX_FMT, SCENES_DIR, SHORT_SLUG_MAXLEN,
    V_CODEC, V_CRF, V_PRESET, VIDEO_DIR, WIDTH,
)
from mux.encoder import MuxError, concat_clips, probe_duration, render_scene_clip, validate_video
from mux.models import MuxInput, MuxPackage, MuxState, SceneClip

# Bumping any of these should re-render cached clips, so they're folded into
# the cache key alongside the caption style version.
_ENCODE_SIG = f"{WIDTH}x{HEIGHT}@{FPS}|{V_CODEC}:{V_CRF}:{V_PRESET}:{PIX_FMT}|{A_CODEC}:{A_BITRATE}:{A_RATE}:{A_CHANNELS}"


def seed(state: MuxInput) -> dict:
    pkg = CaptionPackage.model_validate_json(state["caption_json"])
    scenes = [s.model_dump() for s in pkg.scenes]
    log("mux", f"starting: {len(scenes)} scene(s) for '{pkg.topic}'")
    return {
        "topic": pkg.topic,
        "scenes": scenes,
        "scene_index": 0,
        "retries": 0,
        "clips": [],
        "caveats": [],
        "final_path": "",
        "final_duration": 0.0,
        "mux_package": None,
    }


def _sidecar_text(path) -> str:
    return path.read_text() if path.exists() else ""


def _cache_key(scene_id: str, words: list) -> str:
    """A scene clip is reusable only if the exact inputs that shaped it are
    unchanged: the source video (via its prompt sidecar), the voiceover (via
    audio's cache sidecar), the word timings, the caption look, and the encode
    settings. Any change busts the clip; an untouched scene re-muxes for free."""
    parts = [
        scene_id,
        _sidecar_text(VIDEO_DIR / f"{scene_id}.prompt.txt"),
        _sidecar_text(AUDIO_DIR / f"{scene_id}.prompt.txt"),
        json.dumps(words, sort_keys=True),
        CAPTION_STYLE_VERSION,
        _ENCODE_SIG,
    ]
    return hashlib.sha256("||".join(parts).encode()).hexdigest()


def _slug(topic: str) -> str:
    """Filesystem-safe, human-readable stem from the topic (lowercase,
    non-alphanumeric runs collapsed to '-'), so a finished short is recognizable
    at a glance in output/shorts/."""
    s = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    return s[:SHORT_SLUG_MAXLEN].strip("-") or "short"


def _short_id(topic: str, ok_clips: list) -> str:
    """Content address for the finished short: a hash of the topic plus each
    surviving scene clip's cache key (read from its on-disk sidecar). Identical
    inputs -> identical id -> same filename (idempotent, overwritten in place);
    a different topic or an edited scene -> different id -> a new file that sits
    alongside the old one instead of replacing it."""
    parts = [topic]
    for c in ok_clips:
        sid = c["scene_id"]
        parts.append(f"{sid}:{_sidecar_text(SCENES_DIR / f'{sid}.key.txt')}")
    return hashlib.sha256("||".join(parts).encode()).hexdigest()[:12]


def _skip(state: MuxState, scene_id: str, caveat: str) -> dict:
    log("mux", f"scene ({scene_id}): skipped -- {caveat}")
    clip = {"scene_id": scene_id, "clip_path": "", "duration": 0.0, "status": "skipped"}
    return {
        "clips": state["clips"] + [clip],
        "scene_index": state["scene_index"] + 1,
        "retries": 0,
        "caveats": state["caveats"] + [caveat],
    }


def render_scene(state: MuxState) -> dict:
    scene = state["scenes"][state["scene_index"]]
    scene_id = scene["scene_id"]
    words = scene["words"]
    position = f"{state['scene_index'] + 1}/{len(state['scenes'])}"

    video_path = VIDEO_DIR / f"{scene_id}.mp4"
    audio_path = AUDIO_DIR / f"{scene_id}.wav"
    if not video_path.exists():
        return _skip(state, scene_id, f"Scene {scene_id} skipped: no video at {video_path}")
    if not audio_path.exists():
        return _skip(state, scene_id, f"Scene {scene_id} skipped: no audio at {audio_path}")

    SCENES_DIR.mkdir(parents=True, exist_ok=True)
    dest = SCENES_DIR / f"{scene_id}.mp4"
    keyfile = SCENES_DIR / f"{scene_id}.key.txt"
    cache_key = _cache_key(scene_id, words)

    caveats = list(state["caveats"])
    if not words:
        caveats.append(f"Scene {scene_id} has no caption words; muxed video+audio without captions.")

    # Cache hit: the exact same inputs already produced this clip.
    if dest.exists() and keyfile.exists() and keyfile.read_text() == cache_key:
        dur = probe_duration(dest)
        log("mux", f"scene {position} ({scene_id}): cache hit -> {dest}")
        clip = {"scene_id": scene_id, "clip_path": str(dest), "duration": round(dur, 3), "status": "ok"}
        return {
            "clips": state["clips"] + [clip],
            "scene_index": state["scene_index"] + 1,
            "retries": 0,
            "caveats": caveats,
        }

    attempt = state["retries"] + 1
    log("mux", f"scene {position} ({scene_id}): attempt {attempt}")
    frames_dir = SCENES_DIR / f"{scene_id}_frames"
    try:
        audio_dur = probe_duration(audio_path)
        video_dur = probe_duration(video_path)
        frames = render_scene_captions(words, frames_dir, audio_dur)
        render_scene_clip(scene_id, video_path, audio_path, frames, audio_dur, video_dur, dest)
        validate_video(dest)
        keyfile.write_text(cache_key)
        clip = {"scene_id": scene_id, "clip_path": str(dest), "duration": round(audio_dur, 3), "status": "ok"}
        return {
            "clips": state["clips"] + [clip],
            "scene_index": state["scene_index"] + 1,
            "retries": 0,
            "caveats": caveats,
        }
    except MuxError as e:
        log("mux", f"scene {position} ({scene_id}): attempt {attempt} failed: {e}")
        if attempt >= MAX_RETRIES_PER_SCENE + 1:
            return _skip(state, scene_id, f"Scene {scene_id} skipped after {attempt} attempt(s): {e}")
        return {"retries": attempt}
    finally:
        if frames_dir.exists():
            shutil.rmtree(frames_dir, ignore_errors=True)


def route_after_scene(state: MuxState) -> str:
    if state["scene_index"] >= len(state["scenes"]):
        return "concat"
    return "render_scene"


def concat(state: MuxState) -> dict:
    ok_clips = [c for c in state["clips"] if c["status"] == "ok"]
    if not ok_clips:
        caveat = "No scene clips were produced; final short not assembled."
        log("mux", f"concat: {caveat}")
        return {"final_path": "", "final_duration": 0.0, "caveats": state["caveats"] + [caveat]}

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SCENES_DIR.mkdir(parents=True, exist_ok=True)
    short_id = _short_id(state["topic"], ok_clips)
    dest = OUTPUT_DIR / f"{_slug(state['topic'])}_{short_id}.mp4"
    existed = dest.exists()
    paths = [Path(c["clip_path"]) for c in ok_clips]
    # Throwaway concat manifest kept out of the deliverable dir; removed after.
    list_file = SCENES_DIR / f"concat_{short_id}.txt"
    concat_clips(paths, dest, list_file)
    validate_video(dest)
    dur = probe_duration(dest)
    list_file.unlink(missing_ok=True)
    verb = "re-assembled (identical inputs)" if existed else "assembled"
    log("mux", f"concat: {len(ok_clips)} scene(s) {verb} -> {dest} ({dur:.2f}s); "
               f"earlier shorts in {OUTPUT_DIR} are kept until uploaded")
    return {"final_path": str(dest), "final_duration": round(dur, 3)}


def finalize(state: MuxState) -> dict:
    for c in state["caveats"]:
        log("mux", f"caveat: {c}")

    ok = sum(1 for c in state["clips"] if c["status"] == "ok")
    skipped = sum(1 for c in state["clips"] if c["status"] == "skipped")
    log("mux", f"done: {ok} ok, {skipped} skipped, final={state['final_path'] or '(none)'} "
               f"({state['final_duration']:.2f}s)")

    pkg = MuxPackage(
        topic=state["topic"],
        scene_clips=[SceneClip(**c) for c in state["clips"]],
        final_path=state["final_path"],
        duration=state["final_duration"],
        caveats=state["caveats"],
    )
    return {"mux_package": pkg}
