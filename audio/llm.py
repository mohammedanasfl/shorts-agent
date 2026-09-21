"""ChatGroq client for the audio stage's "delivery director" step -- a single
cheap call that reads the short's tone and picks one narrator voice for the
whole short plus one delivery tag per scene. See research/llm.py for why
load_dotenv() is repeated per-module."""
from pathlib import Path

from dotenv import load_dotenv
from langchain_groq import ChatGroq

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# reasoning_effort="none" avoids qwen3 burning its <think> block (see
# research/llm.py). temperature=0: voice/delivery should stay stable across
# reruns of the same script, not flip-flop the way a creative-writing call
# might. max_tokens=200 covers one VOICE line plus one line per scene (our
# scripts run 5-7 scenes) while staying well under the account's OTPM cap.
director_llm = ChatGroq(model="qwen/qwen3.8-27b", max_tokens=200, reasoning_effort="none", temperature=0)
