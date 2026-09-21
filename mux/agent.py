"""Mux (final-assembly) stage of the Shorts pipeline: CaptionPackage JSON in,
one finished vertical short out. For each scene it trims the generated video to
the voiceover length, muxes the voiceover, and overlays word-timed karaoke
captions (rendered by Pillow, since this ffmpeg build has no libass), then
concatenates every scene clip into output/shorts/final_short.mp4.

Graph structure and the run_mux() entry point only -- node logic lives in
mux/nodes.py, the ffmpeg driver in mux/encoder.py, caption rendering in
mux/captions.py, and constants in mux/config.py. CLI entry point is root main.py.
"""
from langgraph.graph import END, START, StateGraph

from common.log import log
from mux.models import MuxInput, MuxPackage, MuxState
from mux.nodes import concat, finalize, render_scene, route_after_scene, seed


class MuxError(RuntimeError):
    """Raised when the mux graph could not produce a MuxPackage."""


builder = StateGraph(MuxState, input_schema=MuxInput)
builder.add_node("seed", seed)
builder.add_node("render_scene", render_scene)
builder.add_node("concat", concat)
builder.add_node("finalize", finalize)

builder.add_edge(START, "seed")
builder.add_edge("seed", "render_scene")
builder.add_conditional_edges(
    "render_scene",
    route_after_scene,
    {"render_scene": "render_scene", "concat": "concat"},
)
builder.add_edge("concat", "finalize")
builder.add_edge("finalize", END)

graph = builder.compile()


def run_mux(caption_json: str) -> MuxPackage:
    final_state = graph.invoke({"caption_json": caption_json}, config={"recursion_limit": 50})

    package = final_state.get("mux_package")
    if package is None:
        log("mux", "FAILED: no mux_package was produced")
        raise MuxError("Mux failed: no mux_package was produced.")
    return package
