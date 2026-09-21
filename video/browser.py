"""Playwright driver for Gemini's web chat: generates one video clip per call.

Auth model -- attach-only, never launch/login:
Google blocks sign-in itself the moment it detects an automation-controlled
browser ("this browser or app may not be secure"), so a Playwright-launched
or Playwright-driven login can never succeed here. Instead:

  1. One-time, by hand (no debug port -- plain browsing so nothing looks
     automated): quit Chrome fully, then
         open -a "Google Chrome" --args --user-data-dir=<CHROME_PROFILE_DIR>
     and log in to Gemini normally in that window. Quit Chrome fully again.
  2. Every run, before using this module: relaunch the SAME profile with the
     debug port open and leave it running --
         open -a "Google Chrome" --args \\
             --user-data-dir=<CHROME_PROFILE_DIR> \\
             --remote-debugging-port=9222
     (CHROME_PROFILE_DIR and the port are in video/config.py. Chrome only
     opens the debug port on a non-default profile, so this must not be the
     user's everyday Chrome.)

This module only ever attaches to that already-authenticated browser via
connect_over_cdp -- it never launches a browser, never drives a login, and
never sees a password. preflight() checks the port is up before anything
else runs, so a job fails in ~2s with these instructions instead of after
burning a full graph pass.
"""
import atexit
import base64
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from common.log import log
from video.config import (
    ASPECT_RATIO_TARGET,
    ASPECT_RATIO_TOLERANCE,
    CAPACITY_GRACE_SECONDS,
    CDP_URL,
    GEMINI_URL,
    GENERATION_TIMEOUT_SECONDS,
    OUTPUT_DIR,
    POLL_INTERVAL_SECONDS,
)
from video.models import ClipFailureKind

# Verified live against the real Gemini UI (2026-09-21):
# - clicking VIDEO_TOOL_MENU_ITEM closes its menu, so it can't be re-queried
#   afterward -- VIDEO_TOOL_CONFIRMATION (a persistent button that appears
#   once video mode is active) is checked instead.
# - the aspect-ratio trigger's accessible name includes the *current* ratio
#   ("Aspect ratio, Landscape (16:9)"), so it's matched by substring.
VIDEO_TOOL_BUTTON = {"role": "button", "name": "Upload & tools"}
VIDEO_TOOL_MENU_ITEM = {"role": "menuitemcheckbox", "name": "Create video"}
VIDEO_TOOL_CONFIRMATION = {"role": "button", "name": "Deselect Videos"}
ASPECT_RATIO_TRIGGER = {"role": "button", "name": "Aspect ratio"}
ASPECT_RATIO_OPTION = {"role": "menuitemradio", "name": "Portrait (9:16)"}


class NotConnectedError(RuntimeError):
    """Chrome isn't reachable with its debug port open -- see this module's
    docstring for the one-time login + per-run relaunch steps."""


class ClipGenerationError(RuntimeError):
    def __init__(self, kind: ClipFailureKind, message: str):
        super().__init__(message)
        self.kind = kind


_playwright = None
_browser = None


def preflight() -> None:
    try:
        urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2)
    except (urllib.error.URLError, ConnectionError, OSError) as e:
        raise NotConnectedError(
            f"Chrome isn't reachable at {CDP_URL} ({e}). Quit Chrome fully, then "
            f"relaunch the automation profile with the debug port open -- see "
            f"video/browser.py's module docstring for the exact command."
        ) from e


def get_browser():
    """Lazily attaches to (and caches) the CDP-connected browser for the
    whole process. Never launches a browser of its own."""
    global _playwright, _browser
    if _browser is not None:
        return _browser
    preflight()
    _playwright = sync_playwright().start()
    _browser = _playwright.chromium.connect_over_cdp(CDP_URL)
    return _browser


def close_browser() -> None:
    """Detaches from the CDP session -- does NOT quit the user's real Chrome."""
    global _playwright, _browser
    if _browser is not None:
        _browser.close()
    if _playwright is not None:
        _playwright.stop()
    _browser = _playwright = None


atexit.register(close_browser)


def _select_video_tool(page) -> None:
    try:
        page.get_by_role(**VIDEO_TOOL_BUTTON).click()
        page.get_by_role(**VIDEO_TOOL_MENU_ITEM).click()
        # The menu closes (and the item detaches) on selection, so confirm
        # via a separate, persistent element instead of re-querying it.
        page.get_by_role(**VIDEO_TOOL_CONFIRMATION).wait_for(state="visible", timeout=5_000)
    except PlaywrightError as e:
        raise ClipGenerationError(ClipFailureKind.UI_MISS, f"could not select video tool: {e}") from e


def _select_aspect_ratio(page) -> None:
    # Entering video mode reveals a ratio picker defaulting to Landscape --
    # prompt text alone does not override this UI-level default, so it must
    # be set explicitly or every clip comes back landscape.
    try:
        page.get_by_role(**ASPECT_RATIO_TRIGGER).click()
        page.get_by_role(**ASPECT_RATIO_OPTION).click()
    except PlaywrightError as e:
        raise ClipGenerationError(ClipFailureKind.UI_MISS, f"could not select portrait aspect ratio: {e}") from e


def _submit_prompt(page, prompt: str) -> int:
    """Fills and sends the prompt, returning the response index this turn is
    guaranteed to occupy. Captured BEFORE submitting: a freshly opened tab to
    gemini.google.com/app can resume the account's most recent conversation
    instead of starting blank, so trusting "the last response on the page"
    can silently return a previous job's already-finished video instead of
    this one. Anchoring to an index captured before submission makes that
    structurally impossible."""
    try:
        expected_index = page.locator("model-response").count()
        box = page.get_by_role("textbox").first
        box.fill(prompt)
        box.press("Enter")
        return expected_index
    except PlaywrightError as e:
        raise ClipGenerationError(ClipFailureKind.UI_MISS, f"could not submit prompt: {e}") from e


def _poll_for_video_src(page, expected_index: int, scene_id: str):
    """Polls the response at expected_index until a video appears, the turn
    resolves to text instead (classified), or the deadline passes."""
    response = page.locator("model-response").nth(expected_index)
    started = time.time()
    deadline = started + GENERATION_TIMEOUT_SECONDS
    grace_deadline = None
    next_progress_log = started + 30

    while True:
        now = time.time()
        if grace_deadline is not None and now > grace_deadline:
            return None, ClipFailureKind.TRANSIENT_CAPACITY
        if grace_deadline is None and now > deadline:
            return None, ClipFailureKind.STALLED_NO_VIDEO
        if now > next_progress_log:
            log("video", f"{scene_id}: still waiting for render... {int(now - started)}s elapsed")
            next_progress_log = now + 30

        try:
            video = response.locator("generated-video video")
            if video.count() > 0:
                src = video.first.get_attribute("src") or video.first.get_attribute("currentSrc")
                if src:
                    return src, None

            turn_finished = (
                response.locator('[class*="message-actions"]').count() > 0
                and response.locator('.markdown[aria-busy="true"]').count() == 0
            )
            if turn_finished and video.count() == 0 and grace_deadline is None:
                text = response.inner_text()
                if "as soon as your limit resets" in text:
                    return None, ClipFailureKind.DAILY_QUOTA_EXCEEDED
                if "getting a lot of requests" in text or "high demand" in text:
                    # Empirically this can still resolve into a real video
                    # ~47s later without resubmitting -- give it room instead
                    # of failing immediately.
                    log("video", f"{scene_id}: got a high-demand response, waiting up to "
                                  f"{CAPACITY_GRACE_SECONDS}s in case it still resolves")
                    grace_deadline = time.time() + CAPACITY_GRACE_SECONDS
                else:
                    return None, ClipFailureKind.CONTENT_REFUSAL
        except PlaywrightError as e:
            raise ClipGenerationError(ClipFailureKind.SESSION_DEAD, f"browser session died mid-poll: {e}") from e

        time.sleep(POLL_INTERVAL_SECONDS)


def _download(page, src: str, dest: Path) -> None:
    try:
        if src.startswith("blob:"):
            b64 = page.evaluate(
                """async (url) => {
                    const res = await fetch(url);
                    const buf = await res.arrayBuffer();
                    let binary = '';
                    const bytes = new Uint8Array(buf);
                    for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
                    return btoa(binary);
                }""",
                src,
            )
            dest.write_bytes(base64.b64decode(b64))
        else:
            resp = page.request.get(src)
            dest.write_bytes(resp.body())
    except PlaywrightError as e:
        raise ClipGenerationError(ClipFailureKind.INVALID_DOWNLOAD, f"download failed: {e}") from e


def _validate(path: Path) -> None:
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=30, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise ClipGenerationError(ClipFailureKind.INVALID_DOWNLOAD, f"ffprobe failed: {e}") from e

    streams = json.loads(out.stdout).get("streams", [])
    if not streams:
        raise ClipGenerationError(ClipFailureKind.INVALID_DOWNLOAD, "downloaded file has no video stream")

    width, height = streams[0]["width"], streams[0]["height"]
    ratio = width / height
    if abs(ratio - ASPECT_RATIO_TARGET) > ASPECT_RATIO_TOLERANCE:
        log("video", f"warning: clip is {width}x{height} (ratio {ratio:.3f}), "
                      f"not portrait 9:16 -- will need cropping later")


def generate_video(scene_id: str, prompt: str) -> Path:
    """Generates and downloads one clip. Opens its own tab and closes it when
    done (or on failure) -- a stuck/refused turn is abandoned by discarding
    the tab, never by trying to recover it in place."""
    log("video", f"{scene_id}: opening tab")
    browser = get_browser()
    try:
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
    except PlaywrightError as e:
        raise ClipGenerationError(ClipFailureKind.SESSION_DEAD, f"could not open a tab: {e}") from e

    try:
        try:
            page.goto(GEMINI_URL)
        except PlaywrightError as e:
            raise ClipGenerationError(ClipFailureKind.SESSION_DEAD, f"could not load Gemini: {e}") from e

        _select_video_tool(page)
        log("video", f"{scene_id}: video mode selected")

        _select_aspect_ratio(page)
        log("video", f"{scene_id}: portrait 9:16 selected")

        expected_index = _submit_prompt(page, prompt)
        log("video", f"{scene_id}: prompt submitted, waiting for render "
                      f"(timeout {GENERATION_TIMEOUT_SECONDS}s)")

        src, failure_kind = _poll_for_video_src(page, expected_index, scene_id)
        if src is None:
            raise ClipGenerationError(failure_kind, "no video produced for this turn")
        log("video", f"{scene_id}: video ready, downloading")

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        dest = OUTPUT_DIR / f"{scene_id}.mp4"
        _download(page, src, dest)
        _validate(dest)
        # Written only after validation passes, so a later cache check can
        # never trust a corrupt or refused result.
        dest.with_suffix(".prompt.txt").write_text(prompt)
        log("video", f"{scene_id}: done -> {dest}")
        return dest
    finally:
        try:
            page.close()
        except PlaywrightError:
            pass
