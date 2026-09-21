# CLAUDE.md

Guidance for Claude Code working in this repo. Read this before touching code —
it captures conventions and environment facts that aren't obvious from a glance.

## What this is

A 7-stage agentic pipeline that turns a one-line idea into a finished vertical
short: **Research → Script → Video → Audio → Caption → Mux → Upload(planned)**.
Each stage is its own LangGraph `StateGraph` package. See `README.md` and
`docs/architecture.svg` for the full picture. The pipeline is narration-paced:
the script drives everything; the mux stage trims each video clip to its
voiceover length.

**Video and Audio are siblings**, not sequential — both consume the same
`ScriptPackage` (video reads `scene.video_prompt`, audio reads
`scene.narration`). So the audio→caption→mux tail is testable with **zero Gemini
quota**.

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

Files per stage: `config.py` (guardrail constants), `models.py` (pydantic +
TypedDicts), `nodes.py` (node fns), `agent.py` (graph + entry point). Plus stage
specifics: `llm.py`/`prompts.py` (research, script, audio), `tools.py` (research),
`format.py` (script, audio parsing), `browser.py` (video), `tts.py` (audio),
`stt.py` (caption), `captions.py`+`encoder.py` (mux). Shared: `common/log.py`
(stderr + `logs/pipeline.log`), `common/retry.py` (rate-limit backoff).

## Running

```bash
python main.py <stage> [input-file]     # stage in: research|script|video|audio|caption|mux|pipeline
python main.py audio   script.json      # audio/caption/mux tail spends NO Gemini quota
python main.py pipeline context.txt     # full run — DOES spend Gemini video quota
langgraph dev                           # inspect any graph in LangGraph Studio
```

Register a new stage in three places: `main.py` (`STAGES` + `run_pipeline`),
`langgraph.json` (`graphs`), and `pyproject.toml` (`[tool.setuptools] packages`).

## Rate limits to design around

Groq free tier: Orpheus TTS 10 RPM / 100 RPD; whisper-turbo 20 RPM / 2K RPD; the
research LLM account has a low tokens-per-minute ceiling (tool results are
truncated, per-tool call counts are hard-capped in `research/tools.py`). Tavily is
metered/paid → capped to 1 call per research run. Gemini video is the scarcest
resource. The scene-level cache is the main defense — never weaken it casually.

## Secrets — never commit

`.env` (Groq + Tavily keys), `.auth/`, and `.chrome-profile/` (live Gemini session
tokens/cookies) are gitignored and must stay that way. `output/`, `logs/`,
`.langgraph_api/`, and root artifacts (`brief.json`, `script_*.json`,
`script_output.md`) are generated and also gitignored. There were no commits before
the initial one, so no secret has ever entered git history — keep it that way.
