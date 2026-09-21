"""Data shapes for the script-writing stage."""
from typing import Annotated, Optional

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel
from typing_extensions import TypedDict


class Scene(BaseModel):
    """One row of the script table, paired with its own video-generation prompt."""
    scene_id: str
    timing: str
    visual: str
    narration: str
    video_prompt: str


class ScriptPackage(BaseModel):
    """Production-ready script deliverable handed to the (future) media stage."""
    topic: str
    visual_style: str
    color_palette: str
    rendering_tone: str
    scenes: list[Scene]
    markdown: str
    # Derived by script/format.py from parsed narration text, never read from
    # model output -- LLMs are unreliable at self-reported word counts.
    word_count: int
    max_word_count: int
    caveats: list[str] = []


class ScriptState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    topic: str
    max_word_count: int
    target_duration_sec: int
    draft_markdown: str
    visual_style: str
    color_palette: str
    rendering_tone: str
    scenes: list
    word_count: int
    revisions: int
    # Quality-critique loop (separate from the word-count revise loop above):
    # brief_text is the rendered brief so the critic can judge grounding
    # without wading through the writer's whole message history; critiques is
    # the bounded counter route_after_critic checks against MAX_CRITIQUES;
    # critic_verdict is "pass" | "revise" | "" (not yet judged).
    brief_text: str
    critiques: int
    critic_verdict: str
    script_package: Optional[ScriptPackage]
    caveats: list


# Declared as the graph's input_schema so callers (CLI and LangGraph Studio
# alike) only need to supply the research stage's output JSON -- seed parses
# it and fills in every other ScriptState field's starting value.
class ScriptInput(TypedDict):
    brief_json: str
