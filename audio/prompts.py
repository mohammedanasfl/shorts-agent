"""System prompt for the audio stage's "delivery director" step -- kept
separate from graph/node logic, same convention as script/prompts.py."""

# Persona descriptors are short, plausible labels (not vendor-documented
# traits) meant only to give the model *some* grounding to reason from when
# picking a narrator -- it's choosing by description, not by ear.
VOICE_DESCRIPTIONS = """- autumn: warm, calm female voice, good for reflective/soothing topics
- diana: crisp, professional female voice, good for factual/explainer topics
- hannah: youthful, upbeat female voice, good for casual/fun topics
- austin: relaxed, conversational male voice, good for casual/story-driven topics
- daniel: clear, authoritative male voice, good for factual/explainer topics
- troy: energetic, punchy male voice, good for high-energy/hype topics"""

DIRECTOR_SYSTEM_PROMPT = """You are a Voice Director for short-form video narration.
You will receive a short's topic, its overall rendering tone, and a numbered list of its scenes (each scene's on-screen visual action and its spoken narration line).

Your job, in two parts:

1. Pick ONE narrator voice for the ENTIRE short (a narrator must stay consistent scene to scene) from exactly these options:
{voice_descriptions}

2. For EACH scene, pick ONE delivery tag that best fits that scene's content and the short's overall tone, from exactly this list:
{delivery_tags}
Use "natural" when nothing more specific clearly fits -- don't force drama or excitement onto a scene that doesn't call for it.

STRICT OUTPUT FORMAT (a parser reads this -- follow it exactly, no extra commentary):
- First line: "VOICE: <voice>" using one of the six voice names above, lowercase.
- Then exactly one line per scene: "<scene_id>: <tag>" using one of the delivery tags above, lowercase.
- Output nothing else -- no explanations, no headers, no blank lines."""


def build_director_prompt(topic: str, rendering_tone: str, scenes: list) -> str:
    """Renders the per-scene payload the director's user turn needs. `scenes`
    is a list of dicts with scene_id/visual/narration (from ScriptPackage.scenes)."""
    lines = [f"TOPIC: {topic}", f"OVERALL TONE: {rendering_tone}", "SCENES:"]
    for s in scenes:
        lines.append(f"- {s['scene_id']}: visual=\"{s['visual']}\" narration=\"{s['narration']}\"")
    return "\n".join(lines)
