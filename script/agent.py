"""Script-writing stage of the Shorts pipeline: ResearchBrief JSON in, structured ScriptPackage out.

Graph structure and the run_script() entry point only -- node logic lives in
script/nodes.py, prompts in script/prompts.py, parsing in script/format.py,
the model client in script/llm.py, and guardrail constants in script/config.py.
CLI entry point is the root main.py.
"""
from langgraph.graph import END, START, StateGraph

from common.log import log
from script.models import ScriptInput, ScriptPackage, ScriptState
from script.nodes import finalize, generate, revise, route_after_generate, seed


class ScriptError(RuntimeError):
    """Raised when the script graph could not produce a ScriptPackage."""


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------
# input_schema=ScriptInput: the graph (and LangGraph Studio's Input form)
# only requires "brief_json" -- seed parses it and fills in every other
# ScriptState field's starting value.
builder = StateGraph(ScriptState, input_schema=ScriptInput)
builder.add_node("seed", seed)
builder.add_node("generate", generate)
builder.add_node("revise", revise)
builder.add_node("finalize", finalize)

builder.add_edge(START, "seed")
builder.add_edge("seed", "generate")
builder.add_conditional_edges(
    "generate", route_after_generate, {"revise": "revise", "finalize": "finalize"}
)
builder.add_edge("revise", "generate")
builder.add_edge("finalize", END)

graph = builder.compile()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run_script(brief_json: str) -> ScriptPackage:
    final_state = graph.invoke({"brief_json": brief_json}, config={"recursion_limit": 25})

    package = final_state.get("script_package")
    if package is None:
        log("script", "FAILED: no script_package was produced")
        raise ScriptError("Script generation failed: no script_package was produced.")
    return package
