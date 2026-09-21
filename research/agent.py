"""Research & Analysis stage of the Shorts pipeline: context in, structured ResearchBrief out.

Graph structure and the run_research() entry point only -- node logic lives in
research/nodes.py, prompts in research/prompts.py, model clients in research/llm.py,
and guardrail constants in research/config.py. CLI entry point is the root main.py.
"""
from langgraph.graph import END, START, StateGraph

from common.log import log
from research.models import ResearchBrief, ResearchInput, State
from research.nodes import escalate, route_after_model, route_after_tools, seed, synthesize, tool_calling_llm, tools_node


class ResearchError(RuntimeError):
    """Raised when the research graph could not gather any usable research."""


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------
# input_schema=ResearchInput: the graph (and LangGraph Studio's Input form)
# only requires "context" -- seed fills in every other State field so callers
# never have to know about the internal bookkeeping fields' starting values.
builder = StateGraph(State, input_schema=ResearchInput)
builder.add_node("seed", seed)
builder.add_node("tool_calling_llm", tool_calling_llm)
builder.add_node("tools", tools_node)
builder.add_node("synthesize", synthesize)
builder.add_node("escalate", escalate)

builder.add_edge(START, "seed")
builder.add_edge("seed", "tool_calling_llm")
builder.add_conditional_edges(
    "tool_calling_llm", route_after_model, {"tools": "tools", "synthesize": "synthesize"}
)
builder.add_conditional_edges(
    "tools", route_after_tools, {"tool_calling_llm": "tool_calling_llm", "escalate": "escalate"}
)
builder.add_edge("synthesize", END)
builder.add_edge("escalate", END)

graph = builder.compile()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run_research(context: str) -> ResearchBrief:
    final_state = graph.invoke({"context": context}, config={"recursion_limit": 25})

    if final_state.get("stop_reason") == "all_sources_failed" or final_state.get("research_brief") is None:
        error_summary = "; ".join(f"{e['tool']}: {e['message']}" for e in final_state.get("errors", []))
        log("research", f"FAILED: all tool sources errored -- {error_summary}")
        raise ResearchError(f"Research failed: all tool sources errored. Errors: {error_summary}")
    return final_state["research_brief"]
