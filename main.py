"""CLI entry point for the Shorts pipeline. Wires up research, script, video,
audio, caption, and mux as STAGES, plus approval and publishing verbs
(review/approve/reject/upload/comment/authorize-youtube) as MANAGEMENT --
those take a filename or id, not a JSON blob, so they don't fit the STAGES
shape."""
import sys
from pathlib import Path

from common.log import log
from research.agent import run_research
from script.agent import run_script
from video.agent import run_video
from audio.agent import run_audio
from caption.agent import run_caption
from mux.agent import run_mux
from approval.config import APPROVED, PENDING, REJECTED, SHORTS_DIR
from approval.store import list_shorts, mark, read_status, status_path
from upload.config import YOUTUBE_PRIVACY_STATUS
from upload.youtube import (
    UploadAuthError, get_authenticated_service, post_engagement_comment,
    run_oauth_flow, title_from_filename, upload_short,
)


def run_pipeline(context: str, auto_approve: bool = False):
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
    if package.final_path:
        # Mark the finished short for review the instant it exists -- never
        # deleted, only gated, until a human (or --auto-approve) clears it.
        # See approval/config.py for why this is a sidecar, not a graph node.
        mark(package.final_path, APPROVED if auto_approve else PENDING)
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

# Approval-lifecycle and publishing verbs. These aren't pipeline stages --
# they don't take a JSON blob and produce the next one -- so they're
# dispatched separately from STAGES rather than forced into that shape.
MANAGEMENT = {"review", "approve", "reject", "upload", "comment", "authorize-youtube"}


def _usage() -> str:
    return f"Usage: python main.py <{'|'.join(STAGES)}|{'|'.join(MANAGEMENT)}> [input-file] [--auto-approve]"


if __name__ == "__main__":
    args = sys.argv[1:]
    auto_approve = "--auto-approve" in args
    args = [a for a in args if a != "--auto-approve"]

    if not args:
        print(_usage(), file=sys.stderr)
        sys.exit(1)

    verb = args[0]

    if verb in MANAGEMENT:
        if verb == "review":
            shorts = list_shorts()
            if not shorts:
                print("No shorts found in output/shorts/.")
            for s in shorts:
                print(f"{s['status']:9} {s['name']}")
            sys.exit(0)

        if verb == "authorize-youtube":
            # Opens a real browser and blocks on your approval -- never
            # called from anywhere else in this codebase. See
            # upload/youtube.py's module docstring, point 3.
            run_oauth_flow()
            sys.exit(0)

        if verb == "upload":
            if len(args) < 2:
                print("Usage: python main.py upload <short.mp4>", file=sys.stderr)
                sys.exit(1)
            name = args[1]
            status = read_status(name)
            if status != APPROVED:
                print(
                    f"'{name}' is not approved (status={status}). "
                    f"Run: python main.py approve {name}",
                    file=sys.stderr,
                )
                sys.exit(1)
            video_path = SHORTS_DIR / Path(name).name
            if not video_path.exists():
                print(f"No short at {video_path}", file=sys.stderr)
                sys.exit(1)

            title, description, tags = title_from_filename(video_path)
            try:
                youtube = get_authenticated_service()
            except UploadAuthError as e:
                print(str(e), file=sys.stderr)
                sys.exit(1)

            video_id = upload_short(
                youtube, str(video_path), title, description, tags, YOUTUBE_PRIVACY_STATUS
            )
            youtube_url = f"https://youtube.com/shorts/{video_id}"

            if YOUTUBE_PRIVACY_STATUS == "public":
                post_engagement_comment(youtube, video_id, "Thanks for watching -- let me know what you think!")
            else:
                log(
                    "upload",
                    f"skipped engagement comment on {video_id}: video is private "
                    f"(would 403). Once it's public: python main.py comment {video_id} <text>",
                )

            # Delete-on-publish: the short is removed only now that it's
            # actually been published -- see mux/config.py and CLAUDE.md.
            video_path.unlink()
            status_path(name).unlink(missing_ok=True)
            log("upload", f"published {name} -> {youtube_url}")
            print(youtube_url)
            sys.exit(0)

        if verb == "comment":
            if len(args) < 3:
                print("Usage: python main.py comment <video_id> <text>", file=sys.stderr)
                sys.exit(1)
            try:
                youtube = get_authenticated_service()
            except UploadAuthError as e:
                print(str(e), file=sys.stderr)
                sys.exit(1)
            thread_id = post_engagement_comment(youtube, args[1], args[2])
            print(thread_id or "Comment failed (see logs).")
            sys.exit(0)

        # approve / reject
        if len(args) < 2:
            print(f"Usage: python main.py {verb} <short.mp4>", file=sys.stderr)
            sys.exit(1)
        try:
            mark(args[1], APPROVED if verb == "approve" else REJECTED)
        except FileNotFoundError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    if verb not in STAGES:
        print(_usage(), file=sys.stderr)
        sys.exit(1)

    stage = STAGES[verb]
    input_text = Path(args[1]).read_text() if len(args) > 1 else sys.stdin.read()
    result = stage(input_text, auto_approve=auto_approve) if verb == "pipeline" else stage(input_text)
    # research emits ResearchBrief (JSON contract for the next stage); script
    # emits ScriptPackage (its markdown *is* the human-facing deliverable).
    print(result.markdown if hasattr(result, "markdown") else result.model_dump_json(indent=2))
