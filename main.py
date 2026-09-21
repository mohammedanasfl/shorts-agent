"""CLI entry point for the Shorts pipeline. Currently wires up research, script,
video, audio, caption, and mux; the remaining stage (upload) registers into
STAGES as it's built."""
import sys
from pathlib import Path

from common.log import log
from research.agent import run_research
from script.agent import run_script
from video.agent import run_video
from audio.agent import run_audio
from caption.agent import run_caption
from mux.agent import run_mux


def run_pipeline(context: str):
    """Chains research -> script -> video -> audio -> caption -> mux for
    convenience. Each stage stays independently invokable below (and
    independently visible in LangGraph Studio, since they're separate compiled
    graphs) for per-stage debugging -- this just composes them for an
    end-to-end CLI run.

    Note: this spends Gemini's video-generation quota. The audio, caption, and
    mux stages are independently runnable (`main.py audio script.json`,
    `main.py caption audio_package.json`, `main.py mux caption_package.json`)
    without touching that quota."""
    log("pipeline", "stage 1/6: research")
    brief = run_research(context)
    log("pipeline", "stage 2/6: script")
    script = run_script(brief.model_dump_json())
    log("pipeline", "stage 3/6: video")
    run_video(script.model_dump_json())
    log("pipeline", "stage 4/6: audio")
    audio = run_audio(script.model_dump_json())
    log("pipeline", "stage 5/6: caption")
    captions = run_caption(audio.model_dump_json())
    log("pipeline", "stage 6/6: mux")
    package = run_mux(captions.model_dump_json())
    log("pipeline", "done")
    return package


STAGES = {
    "research": run_research,
    "script": run_script,
    "video": run_video,
    "audio": run_audio,
    "caption": run_caption,
    "mux": run_mux,
    "pipeline": run_pipeline,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in STAGES:
        print(f"Usage: python main.py <{'|'.join(STAGES)}> [input-file]", file=sys.stderr)
        sys.exit(1)

    stage = STAGES[sys.argv[1]]
    input_text = Path(sys.argv[2]).read_text() if len(sys.argv) > 2 else sys.stdin.read()
    result = stage(input_text)
    # research emits ResearchBrief (JSON contract for the next stage); script
    # emits ScriptPackage (its markdown *is* the human-facing deliverable).
    print(result.markdown if hasattr(result, "markdown") else result.model_dump_json(indent=2))
