"""Caption stage of the Shorts pipeline: AudioPackage JSON in, per-scene
word-level timestamps out (via Groq's whisper-large-v3-turbo transcribing
each already-generated voiceover) -- the data a future verify/mux stage needs
to burn timed captions into the final video.

Graph structure and the run_caption() entry point only -- node logic lives in
caption/nodes.py, the Groq driver in caption/stt.py, and guardrail constants
in caption/config.py. CLI entry point is the root main.py.
"""
from langgraph.graph import END, START, StateGraph

from common.log import log
from caption.models import CaptionInput, CaptionPackage, CaptionState
from caption.nodes import finalize, generate_scene, route_after_scene, seed


class CaptionError(RuntimeError):
    """Raised when the caption graph could not produce a CaptionPackage."""


builder = StateGraph(CaptionState, input_schema=CaptionInput)
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


def run_caption(audio_json: str) -> CaptionPackage:
    final_state = graph.invoke({"audio_json": audio_json}, config={"recursion_limit": 50})

    package = final_state.get("caption_package")
    if package is None:
        log("caption", "FAILED: no caption_package was produced")
        raise CaptionError("Caption generation failed: no caption_package was produced.")
    return package
