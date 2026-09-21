"""Guardrail constants for the video-generation graph -- structural limits, not
UI/network self-restraint. Playwright automation of a live web UI is inherently
flaky (DOM changes, slow renders, capacity/quota messages instead of a real
result), so this stage bounds and classifies retries instead of trusting the
browser to eventually succeed."""
from pathlib import Path

# --- Auth: attach-only, never launch/login ---------------------------------
# Google blocks sign-in itself the moment it detects an automation-controlled
# browser ("this browser or app may not be secure"), so Playwright never
# launches its own browser or drives a login here. A human signs in by hand
# in a plain Chrome window first; Playwright only ever attaches via CDP to
# that already-authenticated session. Full steps are in video/browser.py's
# module docstring.
CDP_URL = "http://localhost:9222"

# Chrome only opens the remote-debugging port on a non-default profile, so
# this must be a dedicated --user-data-dir, never the user's everyday profile.
CHROME_PROFILE_DIR = Path(__file__).resolve().parent.parent / ".chrome-profile"

GEMINI_URL = "https://gemini.google.com/app"

# --- Per-attempt timing -----------------------------------------------------
GENERATION_TIMEOUT_SECONDS = 360   # stall bound; Veo gen can take minutes
POLL_INTERVAL_SECONDS = 5
# A "high demand" text response can still resolve into a real video without
# resubmitting -- give it this much extra time before treating it as a miss.
CAPACITY_GRACE_SECONDS = 90

# --- Retries -----------------------------------------------------------------
# Hard cap on attempts for any single clip regardless of failure kind.
CLIP_MAX_ATTEMPTS = 4
# Per-failure-kind attempt budgets (kinds: see ClipFailureKind in
# video/models.py). A deterministic content refusal won't succeed on retry,
# so it gets fewer chances than a transient capacity message.
RETRY_BUDGET_BY_KIND = {
    "content_refusal": 1,
    "transient_capacity": 4,
    "stalled_no_video": 2,
    "ui_miss": 2,
    "invalid_download": 2,
    "session_dead": 1,
    "daily_quota_exceeded": 1,
}
TRANSIENT_CAPACITY_BACKOFF_SECONDS = 20

# --- Output & validation -----------------------------------------------------
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output" / "videos"

# A clip outside this band is a caveat (cropped later), not a hard failure.
ASPECT_RATIO_TARGET = 9 / 16
ASPECT_RATIO_TOLERANCE = 0.05
