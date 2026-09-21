"""Graph node and routing functions for the research stage."""
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from common.log import log
from common.retry import invoke_with_retry
from research.config import MAX_ITERATIONS, MAX_TOOL_RESULT_CHARS, TOKEN_BUDGET
from research.llm import llm_with_tools, synth_llm
from research.models import ResearchInput, State
from research.prompts import SYNTHESIS_SYSTEM_PROMPT, TOOL_LOOP_SYSTEM_PROMPT
from research.tools import PER_TOOL_CAP, TOOLS_BY_NAME


def seed(state: ResearchInput) -> dict:
    """Turns the caller's only required input (context) into a full State --
    the single place both the CLI and LangGraph Studio go through, so neither
    has to know or supply the internal bookkeeping fields' starting values."""
    context = state["context"]
    log("research", "starting")
    return {
        "messages": [SystemMessage(content=TOOL_LOOP_SYSTEM_PROMPT), HumanMessage(content=context)],
        "context": context,
        "iterations": 0,
        "tokens": 0,
        "tool_calls_made": {},
        "errors": [],
        "research_brief": None,
        "stop_reason": None,
    }


def tool_calling_llm(state: State) -> dict:
    log("research", f"iteration {state['iterations'] + 1}: calling model")
    response = invoke_with_retry(llm_with_tools, state["messages"])
    usage = getattr(response, "usage_metadata", None) or {}
    return {
        "messages": [response],
        "iterations": state["iterations"] + 1,
        "tokens": state["tokens"] + usage.get("total_tokens", 0),
    }


def route_after_model(state: State) -> str:
    if state["iterations"] >= MAX_ITERATIONS or state["tokens"] >= TOKEN_BUDGET:
        return "synthesize"
    if getattr(state["messages"][-1], "tool_calls", None):
        return "tools"
    return "synthesize"


def tools_node(state: State) -> dict:
    """Custom tool node (not prebuilt ToolNode): enforces per-tool call caps and
    turns failures into ToolMessages + State["errors"] instead of raising, so a
    conditional edge -- not an exception -- decides whether to retry or escalate."""
    tool_calls_made = dict(state["tool_calls_made"])
    errors = list(state["errors"])
    tool_messages = []

    for call in state["messages"][-1].tool_calls:
        name, args, call_id = call["name"], call["args"], call["id"]
        cap = PER_TOOL_CAP.get(name)
        used = tool_calls_made.get(name, 0)
        log("research", f"tool call: {name}({args})")

        if cap is not None and used >= cap:
            tool_messages.append(ToolMessage(
                content=f"{name} query limit reached for this run; use a different source or finish.",
                name=name, tool_call_id=call_id,
            ))
            continue

        source_tool = TOOLS_BY_NAME.get(name)
        if source_tool is None:
            errors.append({"tool": name, "message": "unknown tool requested"})
            tool_messages.append(ToolMessage(
                content=f"ERROR: unknown tool '{name}'", name=name, tool_call_id=call_id,
            ))
            continue

        try:
            result = str(source_tool.invoke(args))
            if len(result) > MAX_TOOL_RESULT_CHARS:
                result = result[:MAX_TOOL_RESULT_CHARS] + "...[truncated]"
            tool_calls_made[name] = used + 1
            tool_messages.append(ToolMessage(content=result, name=name, tool_call_id=call_id))
        except Exception as exc:
            errors.append({"tool": name, "message": str(exc)})
            tool_messages.append(ToolMessage(
                content=f"ERROR from {name}: {exc}. Try a different source.",
                name=name, tool_call_id=call_id,
            ))

    return {"messages": tool_messages, "tool_calls_made": tool_calls_made, "errors": errors}


def route_after_tools(state: State) -> str:
    if sum(state["tool_calls_made"].values()) > 0:
        return "tool_calling_llm"
    errored_tools = {e["tool"] for e in state["errors"]}
    if errored_tools >= set(TOOLS_BY_NAME.keys()):
        return "escalate"
    return "tool_calling_llm"


def synthesize(state: State) -> dict:
    digest_parts = [
        f"[{m.name}]\n{m.content}"
        for m in state["messages"]
        if isinstance(m, ToolMessage)
        and not str(m.content).startswith("ERROR")
        and "query limit reached" not in str(m.content)
    ]
    digest = "\n\n".join(digest_parts) if digest_parts else "(no successful tool results were gathered)"

    # These don't live in ResearchBrief -- the schema is an exact contract for the
    # script-writing stage, so guardrail fallout is reported to stderr instead of
    # bloating the JSON payload with operational noise.
    caveats = [f"{e['tool']}: {e['message']}" for e in state["errors"]]
    if state["iterations"] >= MAX_ITERATIONS:
        caveats.append("Stopped early: reached the max tool-loop iteration cap.")
    if state["tokens"] >= TOKEN_BUDGET:
        caveats.append("Stopped early: reached the token budget for this run.")
    for c in caveats:
        log("research", f"caveat: {c}")

    synth_prompt = [
        SystemMessage(content=SYNTHESIS_SYSTEM_PROMPT),
        HumanMessage(content=f"CONTEXT:\n{state['context']}\n\nRESEARCH FINDINGS:\n{digest}"),
    ]
    brief = invoke_with_retry(synth_llm, synth_prompt)
    log("research", f"done -> brief for '{brief.topic}'")
    return {"research_brief": brief}


def escalate(_state: State) -> dict:
    log("research", "escalating: all sources failed")
    return {"stop_reason": "all_sources_failed"}
