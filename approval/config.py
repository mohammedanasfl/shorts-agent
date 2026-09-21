"""Guardrail + location constants for the approval lifecycle.

This is deliberately NOT a LangGraph stage: it never has to wait on a human,
so it can't be a StateGraph node without either blocking (breaking every
other stage's never-hangs invariant) or faking a pass-through (pointless).
Instead a finished short is marked "pending" the instant mux produces it, and
a human reviews/approves/rejects it out of band via CLI verbs in main.py.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

SHORTS_DIR = _ROOT / "output" / "shorts"     # same dir mux/config.py writes to
STATUS_SUFFIX = ".status.json"               # sidecar: "<short>.mp4.status.json"

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
STATUSES = (PENDING, APPROVED, REJECTED)
