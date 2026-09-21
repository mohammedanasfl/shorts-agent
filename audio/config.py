"""Guardrail constants for the audio-generation graph. Groq's free tier caps
Orpheus TTS at 10 requests/min and 100/day (org-wide), so this stage leans
hard on on-disk caching and bounded retries rather than brute-forcing calls."""
from pathlib import Path

TTS_MODEL = "canopylabs/orpheus-v1-english"
RESPONSE_FORMAT = "wav"  # Orpheus on Groq emits WAV only, 48kHz
MAX_INPUT_CHARS = 200     # hard per-request cap on Orpheus; longer narration is chunked
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output" / "audio"

# Model-decided voice (one per short, for narrator consistency) -- the
# whitelist audio/nodes.py's director step must pick from.
ALLOWED_VOICES = ["autumn", "diana", "hannah", "austin", "daniel", "troy"]
DEFAULT_VOICE = "daniel"  # fallback if the director's pick is invalid or the call fails

# Model-decided delivery (one tag per scene) -- same whitelist discipline.
ALLOWED_DELIVERY_TAGS = ["natural", "cheerful", "excited", "dramatic", "professionally", "deadpan", "whisper"]
DEFAULT_DELIVERY = "natural"

# --- Retries -----------------------------------------------------------------
MAX_RETRIES_PER_SCENE = 2  # bounded skip-with-caveat, same philosophy as video
RATELIMIT_BASE_DELAY = 20  # seconds; linear backoff paces requests under the 10 RPM free-tier cap
