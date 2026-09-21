"""Node logic for the audio-generation graph. See audio/agent.py for wiring."""
from langchain_core.messages import HumanMessage, SystemMessage

from script.models import ScriptPackage

from audio.config import ALLOWED_DELIVERY_TAGS, DEFAULT_DELIVERY, DEFAULT_VOICE, MAX_RETRIES_PER_SCENE, OUTPUT_DIR
from audio.format import normalize_for_tts, parse_director_output
from audio.llm import director_llm
from audio.models import AudioClip, AudioInput, AudioPackage, AudioState
from audio.prompts import DIRECTOR_SYSTEM_PROMPT, VOICE_DESCRIPTIONS, build_director_prompt
from audio.tts import AudioGenerationError, generate_audio
from common.log import log
from common.retry import invoke_with_retry


def seed(state: AudioInput) -> dict:
    pkg = ScriptPackage.model_validate_json(state["script_json"])
    scenes = [s.model_dump() for s in pkg.scenes]
    log("audio", f"starting: {len(scenes)} scene(s) for '{pkg.topic}'")

    scene_ids = [s["scene_id"] for s in scenes]
    try:
        system = DIRECTOR_SYSTEM_PROMPT.format(
            voice_descriptions=VOICE_DESCRIPTIONS, delivery_tags=", ".join(ALLOWED_DELIVERY_TAGS)
        )
        user = build_director_prompt(pkg.topic, pkg.rendering_tone, scenes)
        log("audio", "director: choosing voice + per-scene delivery")
        response = invoke_with_retry(director_llm, [SystemMessage(content=system), HumanMessage(content=user)])
        parsed = parse_director_output(response.content, scene_ids)
    except Exception as e:
        log("audio", f"director call failed, defaulting every scene: {e}")
        parsed = {
            "voice": DEFAULT_VOICE,
            "delivery_tags": {sid: DEFAULT_DELIVERY for sid in scene_ids},
            "caveats": [
                f"Director call failed, defaulted to '{DEFAULT_VOICE}'/'{DEFAULT_DELIVERY}' for every scene: {e}"
            ],
        }

    log("audio", f"voice: {parsed['voice']}")
    return {
        "topic": pkg.topic,
        "scenes": scenes,
        "voice": parsed["voice"],
        "delivery_tags": parsed["delivery_tags"],
        "scene_index": 0,
        "retries": 0,
        "clips": [],
        "caveats": parsed["caveats"],
        "audio_package": None,
    }


def _cached_clip(scene_id: str, cache_key: str):
    """Reuses a clip already on disk if its sidecar (voice + tagged text)
    matches exactly -- an unchanged scene regenerates for free on reruns; an
    edited narration, a changed delivery tag, or a re-picked voice doesn't."""
    path = OUTPUT_DIR / f"{scene_id}.wav"
    sidecar = path.with_suffix(".prompt.txt")
    if path.exists() and sidecar.exists() and sidecar.read_text() == cache_key:
        return path
    return None


def generate_scene(state: AudioState) -> dict:
    scene = state["scenes"][state["scene_index"]]
    scene_id = scene["scene_id"]
    narration = scene["narration"]
    voice = state["voice"]
    tag = state["delivery_tags"].get(scene_id, DEFAULT_DELIVERY)
    normalized = normalize_for_tts(narration)
    tts_input = normalized if tag == "natural" else f"[{tag}] {normalized}"
    cache_key = f"{voice}|{tts_input}"
    position = f"{state['scene_index'] + 1}/{len(state['scenes'])}"

    cached = _cached_clip(scene_id, cache_key)
    if cached is not None:
        log("audio", f"scene {position} ({scene_id}): cache hit -> {cached}")
        clip = {
            "scene_id": scene_id, "narration": narration, "voice": voice,
            "delivery": tag, "audio_path": str(cached), "status": "ok",
        }
        return {"clips": state["clips"] + [clip], "scene_index": state["scene_index"] + 1, "retries": 0}

    attempt = state["retries"] + 1
    log("audio", f"scene {position} ({scene_id}): attempt {attempt} [voice={voice}, delivery={tag}]")
    try:
        path = generate_audio(scene_id, tts_input, voice)
        clip = {
            "scene_id": scene_id, "narration": narration, "voice": voice,
            "delivery": tag, "audio_path": str(path), "status": "ok",
        }
        return {"clips": state["clips"] + [clip], "scene_index": state["scene_index"] + 1, "retries": 0}
    except AudioGenerationError as e:
        log("audio", f"scene {position} ({scene_id}): attempt {attempt} failed: {e}")

        if attempt >= MAX_RETRIES_PER_SCENE + 1:
            caveat = f"Scene {scene_id} skipped after {attempt} attempt(s): {e}"
            log("audio", f"scene {position} ({scene_id}): skipped -- {caveat}")
            clip = {
                "scene_id": scene_id, "narration": narration, "voice": voice,
                "delivery": tag, "audio_path": "", "status": "skipped",
            }
            return {
                "clips": state["clips"] + [clip],
                "scene_index": state["scene_index"] + 1,
                "retries": 0,
                "caveats": state["caveats"] + [caveat],
            }

        return {"retries": attempt}


def route_after_scene(state: AudioState) -> str:
    if state["scene_index"] >= len(state["scenes"]):
        return "finalize"
    return "generate_scene"


def finalize(state: AudioState) -> dict:
    for c in state["caveats"]:
        log("audio", f"caveat: {c}")

    ok = sum(1 for c in state["clips"] if c["status"] == "ok")
    skipped = sum(1 for c in state["clips"] if c["status"] == "skipped")
    log("audio", f"done: {ok} ok, {skipped} skipped")

    pkg = AudioPackage(
        topic=state["topic"],
        clips=[AudioClip(**c) for c in state["clips"]],
        caveats=state["caveats"],
    )
    return {"audio_package": pkg}
