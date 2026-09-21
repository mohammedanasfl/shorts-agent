"""Node logic for the video-generation graph. See video/agent.py for wiring.

Note: NotConnectedError (Chrome/debug-port isn't up) is deliberately NOT
caught here -- it means the browser infra itself isn't available, so no
amount of per-scene retrying will help. It propagates out of the graph so
the caller sees the setup instructions immediately instead of after burning
a full pass over every scene.
"""
import time

from script.models import ScriptPackage

from common.log import log
from video.browser import ClipGenerationError, generate_video
from video.config import CLIP_MAX_ATTEMPTS, OUTPUT_DIR, RETRY_BUDGET_BY_KIND, TRANSIENT_CAPACITY_BACKOFF_SECONDS
from video.models import ClipFailureKind, VideoClip, VideoInput, VideoPackage, VideoState


def seed(state: VideoInput) -> dict:
    pkg = ScriptPackage.model_validate_json(state["script_json"])
    log("video", f"starting: {len(pkg.scenes)} scene(s) for '{pkg.topic}'")
    return {
        "topic": pkg.topic,
        "scenes": [s.model_dump() for s in pkg.scenes],
        "scene_index": 0,
        "retries": 0,
        "clips": [],
        "caveats": [],
        "aborted": False,
        "video_package": None,
    }


def _cached_clip(scene_id: str, prompt: str):
    """Reuses a clip already on disk if its prompt sidecar matches exactly --
    an unedited scene regenerates for free on reruns; an edited one doesn't."""
    path = OUTPUT_DIR / f"{scene_id}.mp4"
    sidecar = path.with_suffix(".prompt.txt")
    if path.exists() and sidecar.exists() and sidecar.read_text() == prompt:
        return path
    return None


def _skip_remaining(state: VideoState, caveat: str) -> dict:
    remaining = state["scenes"][state["scene_index"]:]
    skipped = [
        {"scene_id": s["scene_id"], "video_prompt": s["video_prompt"], "video_path": "", "status": "skipped"}
        for s in remaining
    ]
    return {
        "clips": state["clips"] + skipped,
        "scene_index": len(state["scenes"]),
        "retries": 0,
        "caveats": state["caveats"] + [caveat],
        "aborted": True,
    }


def generate_scene(state: VideoState) -> dict:
    scene = state["scenes"][state["scene_index"]]
    scene_id = scene["scene_id"]
    prompt = scene["video_prompt"]
    position = f"{state['scene_index'] + 1}/{len(state['scenes'])}"

    cached = _cached_clip(scene_id, prompt)
    if cached is not None:
        log("video", f"scene {position} ({scene_id}): cache hit -> {cached}")
        clip = {"scene_id": scene_id, "video_prompt": prompt, "video_path": str(cached), "status": "ok"}
        return {
            "clips": state["clips"] + [clip],
            "scene_index": state["scene_index"] + 1,
            "retries": 0,
        }

    attempt = state["retries"] + 1
    log("video", f"scene {position} ({scene_id}): attempt {attempt}")
    try:
        path = generate_video(scene_id, prompt)
        log("video", f"scene {position} ({scene_id}): ok -> {path}")
        clip = {"scene_id": scene_id, "video_prompt": prompt, "video_path": str(path), "status": "ok"}
        return {
            "clips": state["clips"] + [clip],
            "scene_index": state["scene_index"] + 1,
            "retries": 0,
        }
    except ClipGenerationError as e:
        log("video", f"scene {position} ({scene_id}): attempt {attempt} failed [{e.kind.value}]: {e}")

        if e.kind == ClipFailureKind.SESSION_DEAD:
            caveat = (
                f"Browser session died during scene {scene_id}; skipping it and all "
                f"remaining scenes rather than grinding through a dead session: {e}"
            )
            log("video", f"scene {position} ({scene_id}): aborting run -- {caveat}")
            return _skip_remaining(state, caveat)

        attempts_so_far = attempt
        budget = min(CLIP_MAX_ATTEMPTS, RETRY_BUDGET_BY_KIND.get(e.kind.value, 2))
        if attempts_so_far >= budget:
            caveat = f"Scene {scene_id} skipped after {attempts_so_far} attempt(s) [{e.kind.value}]: {e}"
            log("video", f"scene {position} ({scene_id}): skipped -- {caveat}")
            clip = {"scene_id": scene_id, "video_prompt": prompt, "video_path": "", "status": "skipped"}
            return {
                "clips": state["clips"] + [clip],
                "scene_index": state["scene_index"] + 1,
                "retries": 0,
                "caveats": state["caveats"] + [caveat],
            }

        if e.kind == ClipFailureKind.TRANSIENT_CAPACITY:
            log("video", f"scene {position} ({scene_id}): backing off {TRANSIENT_CAPACITY_BACKOFF_SECONDS}s before retry")
            time.sleep(TRANSIENT_CAPACITY_BACKOFF_SECONDS)
        return {"retries": attempts_so_far}


def route_after_scene(state: VideoState) -> str:
    if state.get("aborted") or state["scene_index"] >= len(state["scenes"]):
        return "finalize"
    return "generate_scene"


def finalize(state: VideoState) -> dict:
    for c in state["caveats"]:
        log("video", f"caveat: {c}")

    ok = sum(1 for c in state["clips"] if c["status"] == "ok")
    skipped = sum(1 for c in state["clips"] if c["status"] == "skipped")
    log("video", f"done: {ok} ok, {skipped} skipped")

    pkg = VideoPackage(
        topic=state["topic"],
        clips=[VideoClip(**c) for c in state["clips"]],
        caveats=state["caveats"],
    )
    return {"video_package": pkg}
