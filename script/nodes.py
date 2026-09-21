"""Graph node and routing functions for the script stage."""
from langchain_core.messages import HumanMessage, SystemMessage

from common.log import log
from common.retry import invoke_with_retry
from research.models import ResearchBrief
from script.config import MAX_REVISIONS
from script.format import brief_to_text, parse_script_markdown
from script.llm import script_llm
from script.models import Scene, ScriptInput, ScriptPackage, ScriptState
from script.prompts import REVISION_FEEDBACK, SCRIPT_SYSTEM_PROMPT


def seed(state: ScriptInput) -> dict:
    """Turns the caller's only required input (a ResearchBrief JSON string)
    into a full ScriptState -- the single place both the CLI and LangGraph
    Studio go through, mirroring research/nodes.py's seed."""
    brief = ResearchBrief.model_validate_json(state["brief_json"])
    log("script", f"starting for '{brief.topic}'")
    return {
        "messages": [SystemMessage(content=SCRIPT_SYSTEM_PROMPT), HumanMessage(content=brief_to_text(brief))],
        "topic": brief.topic,
        "max_word_count": brief.max_word_count,
        "target_duration_sec": brief.target_duration_sec,
        "draft_markdown": "",
        "visual_style": "",
        "color_palette": "",
        "rendering_tone": "",
        "scenes": [],
        "word_count": 0,
        "revisions": 0,
        "script_package": None,
        "caveats": [],
    }


def generate(state: ScriptState) -> dict:
    log("script", f"generating draft (revision {state['revisions']})")
    response = invoke_with_retry(script_llm, state["messages"])
    parsed = parse_script_markdown(response.content)
    log("script", f"draft ready: {parsed['word_count']} words, {len(parsed['scenes'])} scene(s)")
    return {
        "messages": [response],
        "draft_markdown": response.content,
        "visual_style": parsed["visual_style"],
        "color_palette": parsed["color_palette"],
        "rendering_tone": parsed["rendering_tone"],
        "scenes": parsed["scenes"],
        "word_count": parsed["word_count"],
        "caveats": state["caveats"] + parsed["caveats"],
    }


def route_after_generate(state: ScriptState) -> str:
    if state["word_count"] > state["max_word_count"] and state["revisions"] < MAX_REVISIONS:
        return "revise"
    return "finalize"


def revise(state: ScriptState) -> dict:
    feedback = REVISION_FEEDBACK.format(actual=state["word_count"], max_words=state["max_word_count"])
    log("script", f"revising: {state['word_count']} words > {state['max_word_count']} limit")
    return {
        "messages": [HumanMessage(content=feedback)],
        "revisions": state["revisions"] + 1,
    }


def finalize(state: ScriptState) -> dict:
    caveats = list(state["caveats"])
    if state["word_count"] > state["max_word_count"]:
        caveats.append(
            f"Still over budget after {state['revisions']} revision(s): "
            f"{state['word_count']} words > {state['max_word_count']} limit."
        )
    for c in caveats:
        log("script", f"caveat: {c}")

    package = ScriptPackage(
        topic=state["topic"],
        visual_style=state["visual_style"],
        color_palette=state["color_palette"],
        rendering_tone=state["rendering_tone"],
        scenes=[Scene(**s) for s in state["scenes"]],
        markdown=state["draft_markdown"],
        word_count=state["word_count"],
        max_word_count=state["max_word_count"],
        caveats=caveats,
    )
    log("script", f"done -> {len(package.scenes)} scene(s), {package.word_count} words")
    return {"script_package": package}
