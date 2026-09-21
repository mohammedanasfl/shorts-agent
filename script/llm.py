"""ChatGroq client for the script graph."""
from pathlib import Path

from dotenv import load_dotenv
from langchain_groq import ChatGroq

# ChatGroq reads GROQ_API_KEY at construction time below, so this module must
# load .env itself rather than depending on whichever script imports it first.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# reasoning_effort="none" avoids qwen3 burning its <think> block on every call
# (see research/llm.py). max_tokens=950 stays under this Groq account's hard
# 1000 output-tokens-per-minute cap while leaving room for the full 3-section
# deliverable at 5-7 scenes. temperature=0.3: some creative variation across
# scenes/prompts is desirable here (unlike research's disambiguation problem),
# while staying mostly reproducible. No tool binding, no structured output --
# free-text markdown is parsed by script/format.py instead, since forced
# structured output on nested scene arrays is the qwen malform failure mode
# documented in research/models.py.
script_llm = ChatGroq(model="qwen/qwen3.8-27b", max_tokens=950, reasoning_effort="none", temperature=0.3)
