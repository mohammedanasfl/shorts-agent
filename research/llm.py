"""ChatGroq clients for the research graph."""
from pathlib import Path

from dotenv import load_dotenv
from langchain_groq import ChatGroq

from research.models import ResearchBrief
from research.tools import tools

# ChatGroq reads GROQ_API_KEY at construction time below, so this module must
# load .env itself rather than depending on whichever script imports it first.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# reasoning_effort="none" avoids qwen3 burning its <think> block on every call
# (see notebook 3, 3-chatBot_Tools.ipynb, at the repo root) -- with it off the
# model deliberates less, so the system prompt has to spell out when to reach
# for each tool.
# max_tokens is kept under this Groq account's hard 1000 output-tokens-per-minute
# (OTPM) cap (documented in 3-chatBot_Tools.ipynb, confirmed again live here) --
# a single request asking for more than 1000 is rejected outright regardless of
# prior usage, so both calls must fit under that ceiling individually.
# temperature=0: an ambiguous one-line context (e.g. a bare topic word with more
# than one meaning) otherwise gets resolved differently run to run -- observed
# live, same context/prompt, one run went with the wrong sense of the topic
# entirely. Determinism doesn't fix an ambiguous context, but it stops the model
# from randomly flip-flopping between readings of the same input.
llm = ChatGroq(model="qwen/qwen3.8-27b", max_tokens=300, reasoning_effort="none", temperature=0)
llm_with_tools = llm.bind_tools(tools)

synth_llm = ChatGroq(
    model="qwen/qwen3.8-27b", max_tokens=950, reasoning_effort="none", temperature=0
).with_structured_output(ResearchBrief)
