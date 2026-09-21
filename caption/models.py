"""Data shapes for the caption stage."""
from typing import Optional

from pydantic import BaseModel
from typing_extensions import TypedDict


class CaptionWord(BaseModel):
    word: str
    start: float
    end: float


class SceneCaptions(BaseModel):
    """One scene's word-level timestamps, paired back to the audio clip that produced them."""
    scene_id: str
    words: list[CaptionWord]
    status: str  # "ok" | "skipped"


class CaptionPackage(BaseModel):
    """Deliverable handed to the (future) verify/mux stage for caption burn-in."""
    topic: str
    scenes: list[SceneCaptions]
    caveats: list[str] = []


class CaptionState(TypedDict):
    topic: str
    clips: list           # list[dict] from AudioPackage.clips
    scene_index: int
    retries: int
    scenes: list           # accumulator of produced SceneCaptions dicts
    caveats: list
    caption_package: Optional[CaptionPackage]


# Declared as the graph's input_schema so callers only need to supply the
# audio stage's output JSON -- seed parses it and fills in every other
# CaptionState field's starting value.
class CaptionInput(TypedDict):
    audio_json: str
