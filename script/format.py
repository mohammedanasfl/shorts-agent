"""Pure text transforms for the script stage: brief -> prompt text, markdown -> parsed dict.

Kept dependency-free (stdlib re only) and side-effect-free so both directions
are easy to unit test without touching the LLM or the graph.
"""
import re

from research.models import ResearchBrief

SECTION_RE = re.compile(r"^##\s*(Style Directive|Script Table|Video AI Prompts)\s*$", re.MULTILINE)

STYLE_FIELD_PATTERNS = {
    "visual_style": re.compile(r"Selected Style:\s*(.+)"),
    "color_palette": re.compile(r"Visual Theme & Color Palette:\s*(.+)"),
    "rendering_tone": re.compile(r"Rendering Engine Tone:\s*(.+)"),
}

TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$", re.MULTILINE)
TIMING_SCENE_RE = re.compile(r"^(.*?)\s*\((S\d+)\)\s*$")
VIDEO_PROMPT_RE = re.compile(r"^(S\d+)\s*:\s*(.+)$", re.MULTILINE)
VERDICT_RE = re.compile(r"VERDICT:\s*(PASS|REVISE)", re.IGNORECASE)


def brief_to_text(brief: ResearchBrief) -> str:
    """Renders a ResearchBrief into the human-readable payload the script
    system prompt says it will receive."""
    lines = [
        f"TOPIC: {brief.topic}",
        f"TARGET DURATION (sec): {brief.target_duration_sec}",
        f"MAX WORD COUNT: {brief.max_word_count}",
        f"TARGET AUDIENCE: {brief.target_audience}",
        f"TONE: {brief.tone}",
        "HOOKS:",
        *[f"- {h}" for h in brief.hooks],
        "CORE FACTS:",
        *[f"- {f}" for f in brief.core_physics_facts],
        "REAL-WORLD HAZARDS:",
        *[f"- {h}" for h in brief.real_world_hazards],
        "SUGGESTED VISUAL CUES:",
        *[f"- {c}" for c in brief.suggested_visual_cues],
        "SUMMARY:",
        brief.summary,
    ]
    return "\n".join(lines)


def _split_sections(markdown: str) -> dict:
    sections = {}
    matches = list(SECTION_RE.finditer(markdown))
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        sections[match.group(1)] = markdown[start:end].strip()
    return sections


def _parse_style_directive(text: str, caveats: list) -> dict:
    style = {}
    for field, pattern in STYLE_FIELD_PATTERNS.items():
        match = pattern.search(text)
        style[field] = match.group(1).strip() if match else ""
        if not style[field]:
            caveats.append(f"Style Directive is missing a value for '{field}'.")
    return style


def _parse_script_table(text: str, caveats: list) -> list:
    scenes = []
    for row in TABLE_ROW_RE.findall(text):
        cells = [c.strip() for c in row.split("|")]
        if len(cells) < 3:
            continue
        first_cell = cells[0]
        # Skip the header row and the markdown separator row.
        if not first_cell or first_cell.lower() == "time & scene" or set(first_cell) <= {"-", ":"}:
            continue

        match = TIMING_SCENE_RE.match(first_cell)
        if match:
            timing, scene_id = match.group(1).strip(), match.group(2)
        else:
            scene_id = f"S{len(scenes) + 1}"
            timing = first_cell
            caveats.append(f"Could not parse a scene id out of '{first_cell}', defaulted to {scene_id}.")

        scenes.append({
            "scene_id": scene_id,
            "timing": timing,
            "visual": cells[1],
            "narration": cells[2],
            "video_prompt": "",
        })

    if not scenes:
        caveats.append("No scenes could be parsed from the Script Table section.")
    return scenes


def _parse_video_prompts(text: str) -> dict:
    return dict(VIDEO_PROMPT_RE.findall(text))


def parse_script_markdown(markdown: str) -> dict:
    """Defensive parser: skips malformed rows/sections instead of raising, and
    always returns a usable (if partial) dict so a bad generation degrades
    gracefully into caveats rather than crashing the run.

    word_count is computed here, from parsed narration text, and nowhere else
    -- the model is never asked for and never trusted to report this number.
    """
    caveats = []
    sections = _split_sections(markdown)

    style = _parse_style_directive(sections.get("Style Directive", ""), caveats)
    scenes = _parse_script_table(sections.get("Script Table", ""), caveats)
    prompts = _parse_video_prompts(sections.get("Video AI Prompts", ""))

    for scene in scenes:
        prompt = prompts.get(scene["scene_id"], "")
        if not prompt:
            caveats.append(f"No video prompt found for {scene['scene_id']}.")
        scene["video_prompt"] = prompt

    word_count = sum(len(scene["narration"].split()) for scene in scenes)

    return {
        "visual_style": style.get("visual_style", ""),
        "color_palette": style.get("color_palette", ""),
        "rendering_tone": style.get("rendering_tone", ""),
        "scenes": scenes,
        "word_count": word_count,
        "caveats": caveats,
    }


def parse_critic(text: str) -> tuple:
    """Defensive parser for the critic's verdict, same philosophy as
    parse_script_markdown: never raises. An unparseable reply defaults to
    "pass" -- a malformed critique shouldn't force endless churn -- and the
    caller is expected to note that in a caveat."""
    match = VERDICT_RE.search(text)
    verdict = match.group(1).lower() if match else "pass"
    issues = text.split("ISSUES:", 1)[1].strip() if "ISSUES:" in text else text.strip()
    return verdict, issues
