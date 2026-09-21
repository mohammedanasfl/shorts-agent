"""Data shapes for the video-generation stage."""
from enum import Enum
from typing import Optional

from pydantic import BaseModel
from typing_extensions import TypedDict


class ClipFailureKind(str, Enum):
    """Why a single clip attempt produced no usable video. Drives per-kind
    retry budgets in video/config.py -- a deterministic content refusal isn't
    worth retrying the way a transient capacity message is."""
    CONTENT_REFUSAL = "content_refusal"
    TRANSIENT_CAPACITY = "transient_capacity"
    STALLED_NO_VIDEO = "stalled_no_video"
    UI_MISS = "ui_miss"
    INVALID_DOWNLOAD = "invalid_download"
    SESSION_DEAD = "session_dead"
    DAILY_QUOTA_EXCEEDED = "daily_quota_exceeded"


class VideoClip(BaseModel):
    """One generated (or skipped) clip, paired back to the scene that produced it."""
    scene_id: str
    video_prompt: str
    video_path: str  # "" if the scene was skipped
    status: str       # "ok" | "skipped"


class VideoPackage(BaseModel):
    """Deliverable handed to the (future) audio/caption stages."""
    topic: str
    clips: list[VideoClip]
    caveats: list[str] = []


class VideoState(TypedDict):
    topic: str
    scenes: list  # list[dict] from ScriptPackage.scenes
    scene_index: int
    retries: int
    clips: list
    caveats: list
    aborted: bool  # set when a session_dead failure skips all remaining scenes
    video_package: Optional[VideoPackage]


# Declared as the graph's input_schema so callers (CLI and LangGraph Studio
# alike) only need to supply the script stage's output JSON -- seed parses it
# and fills in every other VideoState field's starting value.
class VideoInput(TypedDict):
    script_json: str
