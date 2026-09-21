"""Generic retry-with-backoff helper, reusable across pipeline stages."""
import time

from groq import RateLimitError


def invoke_with_retry(runnable, prompt, max_retries=3, base_delay=20):
    """OTPM is a per-minute *rate*, not a per-request cap like the input-token
    limit -- a rejected request now can succeed a few seconds later once the
    window rolls over, so this retries with backoff instead of failing the run."""
    for attempt in range(max_retries):
        try:
            return runnable.invoke(prompt)
        except RateLimitError:
            if attempt == max_retries - 1:
                raise
            time.sleep(base_delay * (attempt + 1))


def call_with_retry(thunk, max_retries=3, base_delay=20):
    """Same backoff as invoke_with_retry, for callers that aren't a LangChain
    Runnable -- e.g. audio/tts.py's raw `groq` SDK calls."""
    for attempt in range(max_retries):
        try:
            return thunk()
        except RateLimitError:
            if attempt == max_retries - 1:
                raise
            time.sleep(base_delay * (attempt + 1))
