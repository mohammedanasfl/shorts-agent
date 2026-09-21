"""Research tools: Tavily, Wikipedia, Arxiv, and a custom YouTube competitor search."""
import json
import warnings
from pathlib import Path

import wikipedia
from dotenv import load_dotenv
from langchain_community.tools import ArxivQueryRun, WikipediaQueryRun
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_community.utilities import ArxivAPIWrapper, WikipediaAPIWrapper
from langchain_core.tools import tool
from youtube_search import YoutubeSearch

# TavilySearchResults reads TAVILY_API_KEY at construction time below, so this
# module must load .env itself rather than depending on whichever script
# imports it first.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Constructing TavilySearchResults (not calling it) triggers this deprecation
# notice on every process start -- silenced so it doesn't get mistaken for an
# actual tool-call log line at runtime.
warnings.filterwarnings("ignore", message="The class `TavilySearchResults` was deprecated")

# Wikimedia rate-limits the `wikipedia` package's shared default User-Agent.
wikipedia.set_user_agent("ShortsAgentResearchBot/1.0 (mohammed@tarkalabs.com)")

tavily_tool = TavilySearchResults(max_results=3)

wikipedia_tool = WikipediaQueryRun(
    api_wrapper=WikipediaAPIWrapper(top_k_results=2, doc_content_chars_max=1500),
    description="Search Wikipedia for background/factual grounding on a topic.",
)

arxiv_tool = ArxivQueryRun(
    api_wrapper=ArxivAPIWrapper(top_k_results=2, doc_content_chars_max=1500),
    description="Search arXiv for background, only when the topic is technical or scientific.",
)


@tool
def youtube_competitor_search(query: str) -> str:
    """Search YouTube for existing videos on a topic, to find competitor examples and angles.
    Returns a JSON list of objects with title, url, channel, duration, views."""
    try:
        raw_results = YoutubeSearch(query, max_results=5).to_dict()
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    videos = [
        {
            "title": r.get("title"),
            "url": f"https://www.youtube.com{r.get('url_suffix', '')}",
            "channel": r.get("channel"),
            "duration": r.get("duration"),
            "views": r.get("views"),
        }
        for r in raw_results
    ]
    return json.dumps(videos)


tools = [tavily_tool, wikipedia_tool, arxiv_tool, youtube_competitor_search]
TOOLS_BY_NAME = {t.name: t for t in tools}
# Tavily is metered (paid credits, capped monthly quota) -- Wikipedia/Arxiv/YouTube
# are free. Structurally cap Tavily to 1 call/run so credit spend can't run away
# regardless of what the model decides, independent of the prompt-level guidance
# in TOOL_LOOP_SYSTEM_PROMPT (research/prompts.py) that steers it toward the free sources first.
PER_TOOL_CAP = {
    tavily_tool.name: 1,
    wikipedia_tool.name: 2,
    arxiv_tool.name: 2,
    youtube_competitor_search.name: 2,
}
