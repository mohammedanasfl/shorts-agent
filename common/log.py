"""Shared progress logger for every stage. Prints to stderr (same as each
stage already did) AND appends to a persistent file, so a long-running
background job's progress can be checked with `tail -f logs/pipeline.log`
instead of only being visible in a one-shot captured stderr stream."""
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "pipeline.log"


def log(stage: str, message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp}Z [{stage}] {message}"
    print(line, file=sys.stderr)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")
