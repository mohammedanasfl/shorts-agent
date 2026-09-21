"""Data shapes for the audio-generation stage."""
from typing import Optional

from pydantic import BaseModel
from typing_extensions import TypedDict


class AudioClip(BaseModel):
    """One generated (or skipped) voiceover, paired back to the scene that produced it."""
    scene_id: str
    narration: str
    voice: str      # the single voice used for the whole short
    delivery: str   # bracketed tag applied: "natural" or e.g. "dramatic"
    audio_path: str  # "" if the scene was skipped
    status: str       # "ok" | "skipped"


class AudioPackage(BaseModel):
    """Deliverable handed to the (future) caption/verify stages."""
    topic: str
    clips: list[AudioClip]
    caveats: list[str] = []


class AudioState(TypedDict):
    topic: str
    scenes: list          # list[dict] from ScriptPackage.scenes
    voice: str             # one voice for the whole short, from the director step
    delivery_tags: dict    # {scene_id: tag}, from the director step
    scene_index: int
    retries: int
    clips: list
    caveats: list
    audio_package: Optional[AudioPackage]


# Declared as the graph's input_schema so callers (CLI and LangGraph Studio
# alike) only need to supply the script stage's output JSON -- seed parses it
# and fills in every other AudioState field's starting value.
class AudioInput(TypedDict):
    script_json: str
