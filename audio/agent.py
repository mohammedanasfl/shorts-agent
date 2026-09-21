"""Audio-generation stage of the Shorts pipeline: ScriptPackage JSON in,
per-scene .wav voiceovers out (via Groq's Orpheus TTS -- a single "delivery
director" call picks one narrator voice for the whole short plus a per-scene
vocal-delivery tag, then each scene's narration is synthesized).

Graph structure and the run_audio() entry point only -- node logic lives in
audio/nodes.py, the Groq driver in audio/tts.py, prompts in audio/prompts.py,
parsing in audio/format.py, and guardrail constants in audio/config.py.
CLI entry point is the root main.py.
"""
from langgraph.graph import END, START, StateGraph

from common.log import log
from audio.models import AudioInput, AudioPackage, AudioState
from audio.nodes import finalize, generate_scene, route_after_scene, seed


class AudioError(RuntimeError):
    """Raised when the audio graph could not produce an AudioPackage."""


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------
# input_schema=AudioInput: the graph (and LangGraph Studio's Input form) only
# requires "script_json" -- seed parses it and fills in every other
# AudioState field's starting value.
builder = StateGraph(AudioState, input_schema=AudioInput)
builder.add_node("seed", seed)
builder.add_node("generate_scene", generate_scene)
builder.add_node("finalize", finalize)

builder.add_edge(START, "seed")
builder.add_edge("seed", "generate_scene")
builder.add_conditional_edges(
    "generate_scene",
    route_after_scene,
    {"generate_scene": "generate_scene", "finalize": "finalize"},
)
builder.add_edge("finalize", END)

graph = builder.compile()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run_audio(script_json: str) -> AudioPackage:
    # Worst case: N scenes x (MAX_RETRIES_PER_SCENE + 1) attempts, plus seed/finalize.
    final_state = graph.invoke({"script_json": script_json}, config={"recursion_limit": 50})

    package = final_state.get("audio_package")
    if package is None:
        log("audio", "FAILED: no audio_package was produced")
        raise AudioError("Audio generation failed: no audio_package was produced.")
    return package
