"""Guardrail constants for the script graph."""

# One corrective retry if voice-over comes back over max_word_count -- bounded
# so a stubborn overlong draft can't loop forever; still emitted with a
# caveat if the retry doesn't bring it under budget.
MAX_REVISIONS = 1
