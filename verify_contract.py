"""Standalone contract checker for the script stage. Every assertion is
recomputed independently from ScriptPackage.scenes -- never from a model- or
package-reported number -- since LLMs are unreliable at self-reported counts.

Usage: python verify_contract.py <brief.json>
"""
import sys

from research.models import ResearchBrief
from script.agent import run_script


def normalize(text: str) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in text.lower()).split())


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python verify_contract.py <brief.json>", file=sys.stderr)
        sys.exit(1)

    brief_text = open(sys.argv[1]).read()
    brief = ResearchBrief.model_validate_json(brief_text)
    pkg = run_script(brief_text)

    # 1. Word count -- computed independently in Python, NOT pkg.word_count or any LLM number.
    actual = sum(len(s.narration.split()) for s in pkg.scenes)
    assert actual <= brief.max_word_count, f"Script too long: {actual} > {brief.max_word_count}"

    # 2. Scene 1 starts at zero.
    assert pkg.scenes[0].timing.startswith("00:00"), \
        f"First scene must start at 00:00, got {pkg.scenes[0].timing!r}"

    # 3. Opening uses a real hook from the brief (normalized substring match).
    opening = normalize(pkg.scenes[0].narration)
    assert any(normalize(h) in opening for h in brief.hooks), \
        f"Scene 1 doesn't open with a brief hook: {pkg.scenes[0].narration!r}"

    # 4. Structure sanity.
    assert 5 <= len(pkg.scenes) <= 7, f"Expected 5-7 scenes, got {len(pkg.scenes)}"
    assert all(s.video_prompt.strip() for s in pkg.scenes), "Every scene needs a video prompt"

    print(f"PASS: {len(pkg.scenes)} scenes, {actual}/{brief.max_word_count} words, style={pkg.visual_style!r}")
    if pkg.caveats:
        print("Caveats:")
        for c in pkg.caveats:
            print(f"  - {c}")


if __name__ == "__main__":
    main()
