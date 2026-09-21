"""On-disk approval lifecycle for finished shorts: pending -> approved|rejected.

A finished short (output/shorts/<slug>_<id>.mp4, from mux/nodes.py) is never
deleted by a pipeline run -- see mux/config.py. It's only ever removed by a
successful publish. This module tracks the human decision that gates that
publish, as a JSON sidecar next to the video file, so `output/shorts/` stays
the single source of truth (no separate database to go stale).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from common.log import log

from approval.config import APPROVED, PENDING, SHORTS_DIR, STATUS_SUFFIX, STATUSES


def _resolve(short) -> Path:
    """Accepts a bare filename ("foo.mp4"), a name relative to SHORTS_DIR, or
    an absolute path -- so CLI callers can pass whatever `review` printed."""
    path = Path(short)
    if not path.is_absolute():
        path = SHORTS_DIR / path.name
    return path


def status_path(short) -> Path:
    return _resolve(short).parent / f"{_resolve(short).name}{STATUS_SUFFIX}"


def mark(short, status: str, reason: str = "") -> Path:
    if status not in STATUSES:
        raise ValueError(f"Unknown status '{status}', expected one of {STATUSES}")
    video = _resolve(short)
    if not video.exists():
        raise FileNotFoundError(f"No short at {video}")

    sidecar = status_path(video)
    sidecar.write_text(json.dumps({
        "short": video.name,
        "status": status,
        "updated": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
    }, indent=2))
    log("approval", f"{video.name}: {status}" + (f" ({reason})" if reason else ""))
    return sidecar


def read_status(short) -> str:
    """A short with no sidecar yet is implicitly pending -- mux doesn't have
    to write anything for a bare `run_mux` call to still show up in review."""
    sidecar = status_path(short)
    if not sidecar.exists():
        return PENDING
    return json.loads(sidecar.read_text()).get("status", PENDING)


def list_shorts() -> list:
    """Every finished short directly under SHORTS_DIR (non-recursive glob, so
    the scenes/ subdir of per-scene intermediates is never included) paired
    with its current status."""
    if not SHORTS_DIR.exists():
        return []
    shorts = [
        {"name": p.name, "path": str(p), "status": read_status(p)}
        for p in sorted(SHORTS_DIR.glob("*.mp4"))
    ]
    return shorts


def approved_shorts() -> list:
    """What a future Upload stage consumes -- only ever the approved subset."""
    return [Path(s["path"]) for s in list_shorts() if s["status"] == APPROVED]
