"""Data shapes for the mux (final-assembly) stage."""
from typing import Optional

from pydantic import BaseModel
from typing_extensions import TypedDict


class SceneClip(BaseModel):
    """One assembled per-scene clip (video + voiceover + burned captions),
    paired back to the scene that produced it."""
    scene_id: str
    clip_path: str   # "" if skipped
    duration: float  # seconds; 0.0 if skipped
    status: str      # "ok" | "skipped"


class MuxPackage(BaseModel):
    """Final deliverable: the assembled short plus the per-scene clips it was
    built from. final_path is "" if no scene survived to concatenate."""
    topic: str
    scene_clips: list[SceneClip]
    final_path: str
    duration: float
    caveats: list[str] = []


class MuxState(TypedDict):
    topic: str
    scenes: list          # list[dict] from CaptionPackage.scenes (scene_id, words, status)
    scene_index: int
    retries: int
    clips: list           # accumulator of produced SceneClip dicts
    caveats: list
    final_path: str
    final_duration: float
    mux_package: Optional[MuxPackage]


# Declared as the graph's input_schema so callers only need to supply the
# caption stage's output JSON -- seed parses it and resolves each scene's
# video/audio from disk by scene_id.
class MuxInput(TypedDict):
    caption_json: str
