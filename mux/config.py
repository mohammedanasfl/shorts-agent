"""Guardrail + styling constants for the mux (assembly) stage. This stage is
pure local ffmpeg/Pillow work -- no network, no API quota -- so the limits
here are about deterministic output (fixed geometry/fps so every scene clip
concatenates cleanly) and bounded retries on a flaky subprocess, not rate
limits."""
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# --- Source dirs (scene-id-keyed, same convention as every other stage) ------
VIDEO_DIR = _ROOT / "output" / "videos"
AUDIO_DIR = _ROOT / "output" / "audio"

# --- Output ------------------------------------------------------------------
OUTPUT_DIR = _ROOT / "output" / "shorts"           # final short lands here
SCENES_DIR = OUTPUT_DIR / "scenes"                  # per-scene muxed clips (intermediate)
FINAL_NAME = "final_short.mp4"

# --- Geometry / encode (fixed so per-scene clips concat without re-encoding) --
WIDTH = 720
HEIGHT = 1280
FPS = 24
V_CODEC = "libx264"
V_CRF = "20"
V_PRESET = "veryfast"
PIX_FMT = "yuv420p"
A_CODEC = "aac"
A_BITRATE = "192k"
A_RATE = 48000
A_CHANNELS = 2

# --- Karaoke caption styling (rendered by Pillow -> mux/captions.py) ----------
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_SIZE = 64
CAPTION_MAX_WORDS_PER_LINE = 3       # small windows read better on a vertical short
CAPTION_MARGIN_X = 48                # px safe margin each side; wraps within WIDTH - 2*margin
CAPTION_BASELINE_Y = int(HEIGHT * 0.62)  # vertical anchor of the caption block (lower-middle)
CAPTION_LINE_SPACING = 12
COLOR_NORMAL = (255, 255, 255, 255)      # white
COLOR_ACTIVE = (255, 226, 58, 255)       # yellow highlight on the currently-spoken word
COLOR_OUTLINE = (0, 0, 0, 255)           # black outline
OUTLINE_WIDTH = 6
# Bump this when the caption look changes, so cached scene clips re-render.
CAPTION_STYLE_VERSION = "karaoke-v1"

# --- Retries -----------------------------------------------------------------
MAX_RETRIES_PER_SCENE = 2            # bounded skip-with-caveat, same philosophy as the other stages
