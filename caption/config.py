"""Guardrail constants for the caption graph. Groq's free tier for
whisper-large-v3-turbo (20 RPM, 2K RPD, 28.8K audio-seconds/day) is generous
relative to a handful of 5-10s scene clips, so this stage just mirrors
audio/config.py's bounded-retry-then-skip shape without needing the same
degree of caching pressure."""
from pathlib import Path

STT_MODEL = "whisper-large-v3-turbo"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output" / "captions"
AUDIO_DIR = Path(__file__).resolve().parent.parent / "output" / "audio"

MAX_RETRIES_PER_SCENE = 2
RATELIMIT_BASE_DELAY = 20
