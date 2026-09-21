"""Data shapes for the research & analysis stage."""
from typing import Annotated, Optional

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel
from typing_extensions import TypedDict


# All list fields are flat strings, not nested sub-models: live testing showed
# this model reliably produces flat string lists via forced structured-output
# tool-calling but malforms nested object arrays (invalid JSON, then a runaway
# self-correction loop that burns the whole token budget without recovering).
class ResearchBrief(BaseModel):
    """Structured research brief handed to the (future) script-writing stage."""
    topic: str
    target_duration_sec: int
    max_word_count: int
    summary: str
    hooks: list[str]
    core_physics_facts: list[str]
    real_world_hazards: list[str]
    suggested_visual_cues: list[str]
    target_audience: str
    tone: str


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    context: str
    iterations: int
    tokens: int
    tool_calls_made: dict
    errors: list
    research_brief: Optional[ResearchBrief]
    stop_reason: Optional[str]


# Declared as the graph's input_schema so callers (CLI and LangGraph Studio
# alike) only need to supply "context" -- the seed node fills in every other
# State field's starting value.
class ResearchInput(TypedDict):
    context: str
