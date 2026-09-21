"""Pure text transforms for the audio stage: director LLM output -> parsed
{voice, delivery_tags, caveats}, and narration -> TTS-safe text. Kept
dependency-free (stdlib re only) and side-effect-free so it's easy to unit
test without touching the LLM or the graph -- same discipline as
script/format.py."""
import re

from audio.config import ALLOWED_DELIVERY_TAGS, ALLOWED_VOICES, DEFAULT_DELIVERY, DEFAULT_VOICE

VOICE_RE = re.compile(r"^VOICE:\s*(\w+)\s*$", re.MULTILINE | re.IGNORECASE)
DELIVERY_RE = re.compile(r"^(S\d+)\s*:\s*(\w+)\s*$", re.MULTILINE | re.IGNORECASE)

# A digit run fused directly to a word by a hyphen (e.g. "1000-year-old",
# "80-tonne") makes Orpheus read noticeably slower -- confirmed empirically
# against the live API at roughly 2-3x the duration of the same phrase
# space-separated, and it occasionally spells the number out letter by
# letter instead of speaking it. Splitting the hyphen into a space fixes
# both without changing the words themselves.
NUMERAL_HYPHEN_RE = re.compile(r"(\d[\d,]*)-(?=[A-Za-z])")


def normalize_for_tts(text: str) -> str:
    return NUMERAL_HYPHEN_RE.sub(r"\1 ", text)


def parse_director_output(text: str, scene_ids: list[str]) -> dict:
    """Defensive parser: an invalid/missing voice or tag degrades into a
    caveat + a safe default rather than raising, so a malformed director
    call never blocks audio generation."""
    caveats = []

    voice_match = VOICE_RE.search(text)
    voice = voice_match.group(1).lower() if voice_match else ""
    if voice not in ALLOWED_VOICES:
        caveats.append(f"Director returned an invalid/missing voice ('{voice}'); defaulted to '{DEFAULT_VOICE}'.")
        voice = DEFAULT_VOICE

    raw_tags = {sid.upper(): tag.lower() for sid, tag in DELIVERY_RE.findall(text)}
    delivery_tags = {}
    for scene_id in scene_ids:
        tag = raw_tags.get(scene_id.upper(), "")
        if tag not in ALLOWED_DELIVERY_TAGS:
            caveats.append(
                f"Director returned an invalid/missing delivery tag for {scene_id} ('{tag}'); "
                f"defaulted to '{DEFAULT_DELIVERY}'."
            )
            tag = DEFAULT_DELIVERY
        delivery_tags[scene_id] = tag

    return {"voice": voice, "delivery_tags": delivery_tags, "caveats": caveats}
