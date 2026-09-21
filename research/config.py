"""Guardrail constants for the research graph -- structural limits, not model self-restraint."""

# This Groq account is capped at 7000 input-tokens-per-minute -- the raw
# conversation blew past that after just 2-3 tool calls before this cap was
# added, since it's a hard per-request ceiling, not something a retry/backoff
# can wait out. Every tool result is truncated before joining message history.
MAX_ITERATIONS = 8
TOKEN_BUDGET = 6_000
MAX_TOOL_RESULT_CHARS = 600
