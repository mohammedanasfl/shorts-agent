"""Video-generation stage of the Shorts pipeline: ScriptPackage JSON in, per-scene
.mp4 clips out (via Playwright driving Gemini's web chat -- no video API exists
for this project, so the browser is the API surface).

Graph structure and the run_video() entry point only -- node logic lives in
video/nodes.py, the Playwright driver in video/browser.py, and guardrail
constants in video/config.py. CLI entry point is the root main.py.
"""
from langgraph.graph import END, START, StateGraph

from common.log import log
from video.models import VideoInput, VideoPackage, VideoState
from video.nodes import finalize, generate_scene, route_after_scene, seed


class VideoError(RuntimeError):
    """Raised when the video graph could not produce a VideoPackage."""


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------
# input_schema=VideoInput: the graph (and LangGraph Studio's Input form) only
# requires "script_json" -- seed parses it and fills in every other
# VideoState field's starting value.
builder = StateGraph(VideoState, input_schema=VideoInput)
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
def run_video(script_json: str) -> VideoPackage:
    # Worst case: N scenes x (MAX_RETRIES_PER_SCENE + 1) attempts, plus seed/finalize.
    final_state = graph.invoke({"script_json": script_json}, config={"recursion_limit": 50})

    package = final_state.get("video_package")
    if package is None:
        log("video", "FAILED: no video_package was produced")
        raise VideoError("Video generation failed: no video_package was produced.")
    return package
