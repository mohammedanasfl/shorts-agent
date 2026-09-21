"""Guardrail constants for the script graph."""

# One corrective retry if voice-over comes back over max_word_count -- bounded
# so a stubborn overlong draft can't loop forever; still emitted with a
# caveat if the retry doesn't bring it under budget.
MAX_REVISIONS = 1

# Bounded quality-critique rounds (separate from the word-count loop above).
# Once spent, the draft is accepted as-is with a caveat rather than critiqued
# forever -- same never-hangs philosophy as every other stage's retry bound.
MAX_CRITIQUES = 2
