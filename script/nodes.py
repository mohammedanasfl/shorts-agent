"""Graph node and routing functions for the script stage."""
from langchain_core.messages import HumanMessage, SystemMessage

from common.log import log
from common.retry import invoke_with_retry
from research.models import ResearchBrief
from script.config import MAX_CRITIQUES, MAX_REVISIONS
from script.format import brief_to_text, parse_critic, parse_script_markdown
from script.llm import critic_llm, script_llm
from script.models import Scene, ScriptInput, ScriptPackage, ScriptState
from script.prompts import CRITIC_FEEDBACK, CRITIC_SYSTEM_PROMPT, REVISION_FEEDBACK, SCRIPT_SYSTEM_PROMPT


def seed(state: ScriptInput) -> dict:
    """Turns the caller's only required input (a ResearchBrief JSON string)
    into a full ScriptState -- the single place both the CLI and LangGraph
    Studio go through, mirroring research/nodes.py's seed."""
    brief = ResearchBrief.model_validate_json(state["brief_json"])
    brief_text = brief_to_text(brief)
    log("script", f"starting for '{brief.topic}'")
    return {
        "messages": [SystemMessage(content=SCRIPT_SYSTEM_PROMPT), HumanMessage(content=brief_text)],
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
        "brief_text": brief_text,
        "critiques": 0,
        "critic_verdict": "",
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
    # Word-count guardrail satisfied (or its budget is spent) -- hand off to
    # the quality critic rather than finalizing directly.
    return "critic"


def revise(state: ScriptState) -> dict:
    feedback = REVISION_FEEDBACK.format(actual=state["word_count"], max_words=state["max_word_count"])
    log("script", f"revising: {state['word_count']} words > {state['max_word_count']} limit")
    return {
        "messages": [HumanMessage(content=feedback)],
        "revisions": state["revisions"] + 1,
    }


def critic(state: ScriptState) -> dict:
    """Quality gate, separate from the word-count loop above. Judges the
    current draft against the brief and either passes it through to finalize
    or bounces it back to generate with concrete, scene-referenced feedback.
    Bounded by MAX_CRITIQUES so a stubborn REVISE verdict can't loop forever;
    once the budget is spent the draft is accepted as-is with a caveat."""
    if state["critiques"] >= MAX_CRITIQUES:
        log("script", f"critic: budget spent ({MAX_CRITIQUES}), accepting draft as-is")
        return {
            "critic_verdict": "pass",
            "caveats": state["caveats"] + [
                f"Accepted after {state['critiques']} critique round(s) without a PASS verdict."
            ],
        }

    prompt = [
        SystemMessage(content=CRITIC_SYSTEM_PROMPT),
        HumanMessage(content=f"BRIEF:\n{state['brief_text']}\n\nDRAFT:\n{state['draft_markdown']}"),
    ]
    response = invoke_with_retry(critic_llm, prompt)
    verdict, issues = parse_critic(response.content)

    if verdict == "revise":
        log("script", f"critic: REVISE (round {state['critiques'] + 1})")
        return {
            "critic_verdict": "revise",
            "messages": [HumanMessage(content=CRITIC_FEEDBACK.format(issues=issues))],
            "critiques": state["critiques"] + 1,
        }

    log("script", "critic: PASS")
    return {"critic_verdict": "pass"}


def route_after_critic(state: ScriptState) -> str:
    return "generate" if state["critic_verdict"] == "revise" else "finalize"


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
