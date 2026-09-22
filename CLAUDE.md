# CLAUDE.md

Guidance for Claude Code working in this repo. Read this before touching code —
it captures conventions and environment facts that aren't obvious from a glance.

## What this is

A 7-stage agentic pipeline that turns a one-line idea into a finished vertical
short: **Research → Script → Video → Audio → Caption → Mux → Upload**.
Stages 1-6 are each their own LangGraph `StateGraph` package; Upload
(`upload/`) is deliberately not one — see "Upload / publishing" below. See `README.md` and
`docs/architecture.svg` for the full picture. The pipeline is narration-paced:
the script drives everything; the mux stage trims each video clip to its
voiceover length.

**Video and Audio are siblings**, not sequential — both consume the same
`ScriptPackage` (video reads `scene.video_prompt`, audio reads
`scene.narration`). So the audio→caption→mux tail is testable with **zero Gemini
quota**.

A finished short is never auto-published: it's marked `pending` the instant Mux
produces it, and a human clears it via `review`/`approve`/`reject` CLI verbs
(`approval/`) before the future Upload stage will touch it. See "Approval
lifecycle" below.

## Environment facts (these have burned us before)

- **The venv lives ONE LEVEL UP**, at `../venv` (i.e.
  `/Users/anas/Documents/LangGraph-tutorial/venv`), NOT inside this project.
  `python3` on PATH already resolves to it — just use `python3`. Do **not** try
  `venv/bin/python3` from the project dir; it doesn't exist there.
- **ffmpeg is a stripped Homebrew build with NO libass / freetype / fontconfig /
  drawtext and no ass/subtitles filter.** It cannot render text onto video by any
  native filter. It *does* have `overlay`, `scale`, `pad`, `tpad`, `trim`,
  `concat`, `setpts`, `format`, `fps`, `libx264`, `aac`. This is why captions are
  rendered as PNG frames by **Pillow** (`mux/captions.py`) and composited with
  `overlay` (`mux/encoder.py`). Do not "simplify" this back to a subtitle filter —
  it will fail on this machine. Don't reinstall/upgrade the system ffmpeg either;
  every stage depends on it.
- In an ffmpeg `filter_complex`, the commas inside `overlay=...:enable='between(t,a,b)'`
  **must be escaped as `\,`** or the filtergraph parser reads them as filter
  separators.
- **Groq's Whisper SDK returns `.words` as plain dicts** (`{"word","start","end"}`),
  not attribute objects (unlike OpenAI's SDK). `caption/stt.py` handles both, but
  remember this if you touch STT parsing.

## Gemini / Video stage auth (attach-only)

Google blocks sign-in inside an automation-controlled browser, so `video/browser.py`
**never launches a browser or drives a login** — it only attaches to an
already-authenticated Chrome over CDP (port 9222). One-time manual login into
`.chrome-profile/`, then relaunch that profile with `--remote-debugging-port=9222`
before each video run. Exact commands are in `README.md` and the `video/browser.py`
docstring. `preflight()` fails fast (~2s) if the port isn't up.

## Per-stage architecture (all stages follow this)

- `StateGraph(State, input_schema=Input)`; `seed` parses the prior stage's JSON
  and fills all bookkeeping fields; a per-scene node loops via a conditional-edge
  router; `finalize` builds the `<Stage>Package`. Each `agent.py` compiles a
  module-level `graph` and exposes `run_<stage>(json) -> Package` with a bounded
  `recursion_limit`.
- **Bounded retry-then-skip:** a scene is retried a fixed number of times, then
  skipped with a recorded caveat. Stages never hang on one bad scene. Keep this
  invariant when editing node logic.
- **On-disk cache + sidecar:** each stage writes `output/<stage>/S{n}.<ext>` plus
  a sidecar file holding an exact cache key. A rerun reuses a scene only when a
  freshly-computed key string-matches the sidecar. Keys are scoped to the generic
  scene id (`S1`…`S6`), **not** the topic — so switching topics overwrites files.
  This is intentional (one short in flight at a time), not a bug to "fix."
- **The finished short is the exception — it is NOT clobbered.** `mux/nodes.py`
  `concat()` names it `output/shorts/<slug>_<short_id>.mp4`, where `short_id` is a
  content hash of the topic + surviving scene cache keys. Different topics/versions
  accumulate; identical inputs overwrite the same name (idempotent). Finished
  shorts are removed only by a successful publish (the planned Upload stage deletes
  the file it uploads) — never keyed back to a fixed `final_short.mp4`. Don't
  reintroduce a fixed final filename; that's the flaw this replaced.
- **Script has two independent bounded loops, not one.** `generate` first loops
  through `revise` on a mechanical word-count check (`MAX_REVISIONS`, unchanged).
  Once that passes, `critic` — a separate low-temperature LLM call — judges hook
  strength, grounding-in-brief, and scene coherence, and can bounce the draft back
  to `generate` with concrete feedback (`MAX_CRITIQUES`, `script/config.py`). Once
  the critique budget is spent the draft is accepted as-is with a caveat, same
  degrade-not-hang philosophy as everywhere else. This is deliberately *not*
  merged into the word-count check: it's a quality gate, not a length gate, and it
  only runs (spending Groq tokens) once the draft is already on-budget.

Files per stage: `config.py` (guardrail constants), `models.py` (pydantic +
TypedDicts), `nodes.py` (node fns), `agent.py` (graph + entry point). Plus stage
specifics: `llm.py`/`prompts.py` (research, script, audio), `tools.py` (research),
`format.py` (script, audio parsing), `browser.py` (video), `tts.py` (audio),
`stt.py` (caption), `captions.py`+`encoder.py` (mux). Shared: `common/log.py`
(stderr + `logs/pipeline.log`), `common/retry.py` (rate-limit backoff).

## Approval lifecycle (`approval/`)

Not a LangGraph stage — deliberately. A human can't be waited on inside a
`StateGraph.invoke()` without either blocking (breaking every other stage's
never-hangs invariant) or faking a pass-through. Instead: `run_pipeline` marks
each finished short `pending` in `approval/store.py` (a JSON sidecar next to the
`.mp4`, `output/shorts/<name>.mp4.status.json`) the instant Mux produces it, and
a human reviews out of band with CLI verbs. A short with no sidecar yet is
*implicitly* pending, so a bare `main.py mux` run also shows up in `review`.
Reject never deletes the file — only a future Upload's successful publish does.

```bash
python main.py review                  # list every output/shorts/*.mp4 with its status
python main.py approve <short.mp4>      # -> approved (name or path; resolved under output/shorts/)
python main.py reject  <short.mp4>      # -> rejected (file is kept, not deleted)
python main.py pipeline context.txt --auto-approve   # skip human review for headless/batch runs
```

## Upload / publishing (`upload/`)

Also not a LangGraph stage, and not a `<stage>/config.py, models.py, nodes.py,
agent.py` package like stages 1-6 — deliberately kept to two files
(`upload/config.py`, `upload/youtube.py`) as a **minimal drop-in** of a
verified, working YouTube Data API v3 client, not a rebuild of it. Don't add
`upload/nodes.py`/`agent.py`/a `StateGraph` unless there's an actual reason to
(e.g. it needs to loop or fan out) — there wasn't one, so there isn't one.

`upload_short()`/`post_engagement_comment()`/`get_authenticated_service()`/
`run_oauth_flow()` carry four confirmed, tested constraints about the real
API in their module docstring — read it before touching that file. In short:
(1) there's no comment-pin endpoint, only post; (2) an unaudited Cloud
Console project forces every upload to private regardless of what's
requested, so `upload/config.py`'s `YOUTUBE_PRIVACY_STATUS` defaults to
`"private"`; (3) `run_oauth_flow()` opens a real browser and blocks — it is
**only** ever called from `python main.py authorize-youtube`, never
automatically, never from `run_pipeline`; (4) a private video 403s on
`commentThreads.insert`, so `main.py`'s `upload` verb skips the
auto-comment when the upload is private and logs a caveat pointing at the
manual `comment` verb instead.

Title/description/tags are derived mechanically from the short's own
filename (`upload/youtube.py::title_from_filename()`) rather than from
`ScriptPackage` — the generic-scene-id intermediates that produced the short
may already be overwritten by a later topic by the time a human gets around
to approving it, but the content-addressed filename never is.

`main.py`'s `upload` verb enforces the approval gate in code (refuses to run
`get_authenticated_service()`/`upload_short()` at all unless
`approval.store.read_status(...) == APPROVED`) and deletes the local `.mp4` +
its `.status.json` sidecar only after a successful `upload_short()` call —
the delete-on-publish half of the flaw fixed earlier in this project.

```bash
python main.py authorize-youtube        # one-time, interactive; opens a browser
python main.py upload <short.mp4>       # must be approved first; deletes on success
python main.py comment <video_id> "..." # manual workaround for constraint 4, once public
```

## Running

```bash
python main.py <stage> [input-file]     # stage in: research|script|video|audio|caption|mux|pipeline
python main.py audio   script.json      # audio/caption/mux tail spends NO Gemini quota
python main.py pipeline context.txt     # full run — DOES spend Gemini video quota
langgraph dev                           # inspect any graph in LangGraph Studio
```

Register a new *graph* stage in three places: `main.py` (`STAGES` +
`run_pipeline`), `langgraph.json` (`graphs`), and `pyproject.toml`
(`[tool.setuptools] packages`). `approval/` and `upload/` are intentionally
*not* in `langgraph.json` — neither is a StateGraph (see "Approval lifecycle"
and "Upload / publishing" above) — but both still need the `pyproject.toml`
packages entry and a `main.py` `MANAGEMENT` entry.

## Rate limits to design around

Groq free tier: Orpheus TTS 10 RPM / 100 RPD; whisper-turbo 20 RPM / 2K RPD; the
research LLM account has a low tokens-per-minute ceiling (tool results are
truncated, per-tool call counts are hard-capped in `research/tools.py`). Tavily is
metered/paid → capped to 1 call per research run. Gemini video is the scarcest
resource. The scene-level cache is the main defense — never weaken it casually.
The Script critic (`script/nodes.py::critic`) adds one more Groq call per
critique round on the same low-TPM account — bounded by `MAX_CRITIQUES` and
wrapped in `invoke_with_retry`, same as every other LLM call in this stage.

## Secrets — never commit

`.env` (Groq + Tavily keys), `.auth/` (live Gemini session tokens/cookies at
`.auth/gemini/`, and YouTube OAuth credentials at `.auth/youtube/client_secret.json` +
`.auth/youtube/token.json`), and `.chrome-profile/` are gitignored and must
stay that way — the whole `.auth/` directory is blanket-ignored, so a new
per-service subdirectory under it needs no `.gitignore` edit. `output/`,
`logs/`, `.langgraph_api/`, and root artifacts (`brief.json`, `script_*.json`,
`script_output.md`) are generated and also gitignored. There were no commits
before the initial one, so no secret has ever entered git history — keep it
that way.
