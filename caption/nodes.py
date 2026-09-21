"""Node logic for the caption graph. See caption/agent.py for wiring."""
import json

from audio.models import AudioPackage

from caption.config import AUDIO_DIR, MAX_RETRIES_PER_SCENE, OUTPUT_DIR
from caption.models import CaptionInput, CaptionPackage, CaptionState, SceneCaptions
from caption.stt import CaptionGenerationError, transcribe_scene
from common.log import log


def seed(state: CaptionInput) -> dict:
    pkg = AudioPackage.model_validate_json(state["audio_json"])
    clips = [c.model_dump() for c in pkg.clips]
    log("caption", f"starting: {len(clips)} scene(s) for '{pkg.topic}'")

    return {
        "topic": pkg.topic,
        "clips": clips,
        "scene_index": 0,
        "retries": 0,
        "scenes": [],
        "caveats": [],
        "caption_package": None,
    }


def _audio_cache_key(scene_id: str) -> str:
    """The exact cache key audio/tts.py wrote for this scene (f"{voice}|{tts_input}")
    -- read straight from its sidecar so caption's own cache stays in lockstep
    with whatever audio is currently on disk, with no duplicated logic."""
    sidecar = AUDIO_DIR / f"{scene_id}.prompt.txt"
    return sidecar.read_text() if sidecar.exists() else ""


def _cached_words(scene_id: str, cache_key: str):
    path = OUTPUT_DIR / f"{scene_id}.json"
    sidecar = path.with_suffix(".cachekey.txt")
    if cache_key and path.exists() and sidecar.exists() and sidecar.read_text() == cache_key:
        return json.loads(path.read_text())
    return None


def generate_scene(state: CaptionState) -> dict:
    clip = state["clips"][state["scene_index"]]
    scene_id = clip["scene_id"]
    position = f"{state['scene_index'] + 1}/{len(state['clips'])}"

    if clip["status"] != "ok":
        log("caption", f"scene {position} ({scene_id}): audio was skipped, skipping caption too")
        scene = {"scene_id": scene_id, "words": [], "status": "skipped"}
        return {"scenes": state["scenes"] + [scene], "scene_index": state["scene_index"] + 1, "retries": 0}

    cache_key = _audio_cache_key(scene_id)
    cached = _cached_words(scene_id, cache_key)
    if cached is not None:
        log("caption", f"scene {position} ({scene_id}): cache hit -> {len(cached)} word(s)")
        scene = {"scene_id": scene_id, "words": cached, "status": "ok"}
        return {"scenes": state["scenes"] + [scene], "scene_index": state["scene_index"] + 1, "retries": 0}

    attempt = state["retries"] + 1
    log("caption", f"scene {position} ({scene_id}): attempt {attempt}")
    try:
        words = transcribe_scene(scene_id, clip["audio_path"])
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUTPUT_DIR / f"{scene_id}.json"
        path.write_text(json.dumps(words))
        path.with_suffix(".cachekey.txt").write_text(cache_key)
        scene = {"scene_id": scene_id, "words": words, "status": "ok"}
        return {"scenes": state["scenes"] + [scene], "scene_index": state["scene_index"] + 1, "retries": 0}
    except CaptionGenerationError as e:
        log("caption", f"scene {position} ({scene_id}): attempt {attempt} failed: {e}")

        if attempt >= MAX_RETRIES_PER_SCENE + 1:
            caveat = f"Scene {scene_id} caption skipped after {attempt} attempt(s): {e}"
            log("caption", f"scene {position} ({scene_id}): skipped -- {caveat}")
            scene = {"scene_id": scene_id, "words": [], "status": "skipped"}
            return {
                "scenes": state["scenes"] + [scene],
                "scene_index": state["scene_index"] + 1,
                "retries": 0,
                "caveats": state["caveats"] + [caveat],
            }

        return {"retries": attempt}


def route_after_scene(state: CaptionState) -> str:
    if state["scene_index"] >= len(state["clips"]):
        return "finalize"
    return "generate_scene"


def finalize(state: CaptionState) -> dict:
    for c in state["caveats"]:
        log("caption", f"caveat: {c}")

    ok = sum(1 for s in state["scenes"] if s["status"] == "ok")
    skipped = sum(1 for s in state["scenes"] if s["status"] == "skipped")
    log("caption", f"done: {ok} ok, {skipped} skipped")

    pkg = CaptionPackage(
        topic=state["topic"],
        scenes=[SceneCaptions(**s) for s in state["scenes"]],
        caveats=state["caveats"],
    )
    return {"caption_package": pkg}
