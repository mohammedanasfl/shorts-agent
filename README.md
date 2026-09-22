# Shorts-agents

An agentic pipeline that turns a one-line idea into a finished vertical short
(YouTube Shorts / TikTok / Reels). Each stage is an independent
[LangGraph](https://langchain-ai.github.io/langgraph/) `StateGraph`, so stages
can run one at a time for debugging or be chained end-to-end.

![Shorts-agents pipeline architecture](docs/architecture.png)

<sub>Diagram source: [`docs/architecture.svg`](docs/architecture.svg)</sub>

```
context ──▶ Research ──▶ Script ──┬─▶ Video ─────────────┐
                                  └─▶ Audio ─▶ Caption ─▶ Mux ─▶ final short ─▶ Human Approval ─▶ Upload
```

The pipeline is deliberately **narration-paced**: the script drives everything,
each scene gets its own generated video clip and voiceover, and the final short
trims every clip to its voiceover length before muxing.

---

## Pipeline stages

| # | Stage | Input | Output | Engine |
|---|-------|-------|--------|--------|
| 1 | **Research** (`research/`) | free-text context | `ResearchBrief` (topic, hooks, facts, hazards, visual cues, tone) | Groq `qwen` + Tavily / Wikipedia / arXiv / YouTube search |
| 2 | **Script** (`script/`) | `ResearchBrief` JSON | `ScriptPackage` (per-scene visual + narration + video prompt, plus a markdown deliverable) — passes a bounded word-count loop *and* an LLM quality critic before it's accepted | Groq `qwen` |
| 3 | **Video** (`video/`) | `ScriptPackage` JSON | one `S{n}.mp4` clip per scene, 720×1280 portrait | Gemini (Veo) driven via CDP-attached Playwright |
| 4 | **Audio** (`audio/`) | `ScriptPackage` JSON | one `S{n}.wav` voiceover per scene | Groq Orpheus TTS |
| 5 | **Caption** (`caption/`) | `AudioPackage` JSON | word-level timings per scene | Groq `whisper-large-v3-turbo` STT |
| 6 | **Mux** (`mux/`) | `CaptionPackage` JSON | `output/shorts/<slug>_<id>.mp4` | ffmpeg + Pillow-rendered karaoke captions |
| 7 | **Upload** (`upload/`) | an **approved** short's filename | published YouTube video (id + URL); local file deleted | YouTube Data API v3 |

**Audio is a sibling of Video, not downstream of it** — both consume the
`ScriptPackage` (audio reads each scene's `narration`, video reads its
`video_prompt`). This means the audio → caption → mux tail can be built and
tested without spending any Gemini video quota.

### Shared conventions across every stage

- **Same shape.** Each stage is a `StateGraph(State, input_schema=Input)` with a
  `seed` node that parses the previous stage's JSON, a per-scene loop routed by a
  conditional edge, and a `finalize` node. Each compiles a `graph` and exposes a
  `run_<stage>(json) -> Package` entry point (`recursion_limit` bounded).
- **Bounded retry-then-skip.** No stage hangs on a bad scene. Failures are
  classified, retried a fixed number of times, then the scene is skipped with a
  recorded caveat so the run always completes.
- **On-disk caching.** Every stage writes `output/<stage>/S{n}.<ext>` plus a
  sidecar holding an exact cache key. A rerun reuses a scene only when a freshly
  computed key string-matches the sidecar — so unchanged scenes cost zero API
  calls, and edited scenes regenerate exactly. Cache keys are scoped to the
  generic scene id (`S1`…`S6`), so **switching topics overwrites files** — this
  is intentional (one short in flight at a time).

---

## Setup

### Requirements

- Python **3.11+**
- **ffmpeg / ffprobe** on `PATH` (used by video validation and the mux stage)
- API keys: **Groq** (LLM, TTS, STT) and **Tavily** (research web search)
- Google Chrome + an authenticated Gemini account (only for the Video stage)
- A Google Cloud Console project + OAuth client (only for the Upload stage —
  see "Upload — YouTube publishing" below)

> **Note on ffmpeg:** the mux stage assumes an ffmpeg build **without** libass /
> freetype / drawtext (the common Homebrew stripped build). That's why captions
> are rendered as PNG frames by **Pillow** and composited with the `overlay`
> filter rather than burned in with a subtitle filter. If your ffmpeg *does* have
> those, the Pillow path still works unchanged.

### Install

```bash
python3 -m venv venv          # or reuse an existing venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium   # only needed for the Video stage

cp .env.example .env          # then fill in GROQ_API_KEY and TAVILY_API_KEY
```

---

## Usage

Run any single stage, piping the previous stage's JSON output in via a file or
stdin. The CLI dispatches on the first argument (`main.py`):

```bash
# Full end-to-end (spends Gemini video quota)
python main.py pipeline context.txt

# One stage at a time — each reads the prior stage's JSON
python main.py research context.txt          > brief.json
python main.py script   brief.json            > script.json
python main.py video    script.json           # writes output/videos/S*.mp4
python main.py audio    script.json           > audio.json      # sibling of video
python main.py caption  audio.json            > caption.json
python main.py mux      caption.json          # writes output/shorts/<slug>_<id>.mp4

# Review & approve before Upload will touch anything
python main.py review                         # list every finished short + status
python main.py approve <short.mp4>             # -> approved
python main.py reject  <short.mp4>             # -> rejected (kept on disk, not deleted)
python main.py pipeline context.txt --auto-approve   # skip human review for a batch run

# Publish an approved short to YouTube
python main.py authorize-youtube               # one-time, interactive (opens a browser)
python main.py upload <short.mp4>              # must be approved; deletes the local file on success
python main.py comment <video_id> "..."        # manual engagement comment, once the video is public
```

- `research` / `script` print their result as JSON / markdown to stdout.
- `video` / `audio` / `caption` / `mux` also write their artifacts under
  `output/` (gitignored).
- The **audio → caption → mux** tail runs entirely on Groq — no Gemini quota.

### Output & retention

Finished shorts are written to `output/shorts/<topic-slug>_<id>.mp4`, where `<id>`
is a content hash of the topic and its scene clips. This means:

- A new run **never clobbers a previous deliverable** — different topics (and
  edited versions of the same topic) accumulate side by side.
- Re-running with **identical inputs is idempotent** — same filename, overwritten
  in place, so you don't pile up duplicate copies.

Finished shorts are only ever removed by a successful publish (the **Upload**
stage deletes the specific file it uploads, and only that one). Everything under
`output/` is gitignored. The per-scene intermediates (`output/videos`,
`output/audio`, `output/captions`, `output/shorts/scenes`) are keyed by generic
scene id and *are* overwritten when the topic changes — only the final short is
retained.

### Review & approval

A finished short is never auto-published. `run_pipeline` marks it `pending` the
moment Mux produces it (a JSON sidecar, `output/shorts/<name>.mp4.status.json` —
a bare `main.py mux` run with no sidecar yet is *implicitly* pending too), and a
human clears it with:

```bash
python main.py review                # list every short + its status
python main.py approve <short.mp4>   # -> approved
python main.py reject  <short.mp4>   # -> rejected (file kept, never deleted here)
```

`--auto-approve` on `pipeline` skips this for headless/batch runs. This is a
deliberate **out-of-band** lifecycle (CLI verbs + a status sidecar), not a
LangGraph stage — a human can't be waited on inside a graph invocation without
either blocking (breaking the never-hangs invariant every other stage follows)
or faking the wait. `approval/store.py` holds the read/write logic; `upload`
(below) refuses to run on anything that isn't `approved`.

### Upload — YouTube publishing

`upload/` publishes an **approved** short to YouTube via the Data API v3. It's
intentionally two files (`config.py`, `youtube.py`), not a LangGraph stage —
a minimal, direct wrapper around Google's client, not a rebuild of it.

**One-time setup:**
1. In [Google Cloud Console](https://console.cloud.google.com/), create a
   project, enable the **YouTube Data API v3**, and create an OAuth client
   (type **Desktop app**). Download it and save as
   `.auth/youtube/client_secret.json` (the whole `.auth/` directory is
   gitignored already — no extra setup needed).
2. `python main.py authorize-youtube` — opens a real browser once, you approve
   access, writes `.auth/youtube/token.json`. Every later `upload`/`comment`
   call refreshes silently from that file, no browser involved.

**Per short:**
```bash
python main.py upload <short.mp4>       # must already be `approved` via review/approve
```
Title/description/tags are derived from the short's own filename (the one
thing still guaranteed to exist by the time a human gets around to approving
it — see `upload/youtube.py::title_from_filename()`). On a successful upload
the local `.mp4` and its `.status.json` sidecar are deleted — the
delete-on-publish half of the flaw this project fixed earlier.

**Two real API constraints worth knowing before you touch this:**
- **An unaudited Cloud Console project forces every upload to private**,
  regardless of what's requested — this is why `upload/config.py`'s
  `YOUTUBE_PRIVACY_STATUS` defaults to `"private"`, not `"public"`. Flip it
  once your project passes Google's compliance audit.
- **A private video 403s on any comment posted through the API** until it's
  actually public. `upload` skips the auto-engagement-comment when the
  upload is private (it would just fail every time) — use
  `python main.py comment <video_id> "..."` by hand once the video is public.

Full rationale (plus the "no comment-pin endpoint" and "OAuth needs a real
browser" constraints) is in `upload/youtube.py`'s module docstring.

### Video stage — one-time Gemini auth

Google blocks sign-in inside an automation-controlled browser, so this stage
**attaches** to an already-authenticated Chrome via CDP; it never launches a
browser or drives a login.

1. **Once, by hand** — log in to Gemini in a dedicated profile (no debug port, so
   nothing looks automated):
   ```bash
   open -a "Google Chrome" --args --user-data-dir="$(pwd)/.chrome-profile"
   ```
   Sign in to Gemini normally, then quit Chrome fully.
2. **Before each video run** — relaunch that same profile with the debug port
   open and leave it running:
   ```bash
   open -a "Google Chrome" --args \
       --user-data-dir="$(pwd)/.chrome-profile" \
       --remote-debugging-port=9222
   ```

`video/browser.py` preflight-checks port 9222 and fails fast (~2s) with these
instructions if Chrome isn't reachable. The `.chrome-profile/` and `.auth/`
directories hold live session tokens and are **gitignored** — never commit them.

### LangGraph Studio

Every stage is registered in `langgraph.json`, so you can inspect and step
through any graph individually:

```bash
langgraph dev
```

---

## Project layout

```
common/     shared logging (logs/pipeline.log) and rate-limit retry helpers
research/   stage 1 — brief from context (LLM + search tools)
script/     stage 2 — scenes + narration + per-scene video prompts, gated by a
            word-count loop and an LLM quality critic
video/      stage 3 — Playwright→Gemini clip generation (browser.py)
audio/      stage 4 — Orpheus TTS voiceover (tts.py), model-chosen voice/delivery
caption/    stage 5 — whisper STT word timings (stt.py)
mux/        stage 6 — ffmpeg assembly (encoder.py) + Pillow karaoke (captions.py)
approval/   out-of-band review lifecycle (pending/approved/rejected sidecars) —
            not a LangGraph stage; see "Review & approval" above
upload/     stage 7 — YouTube Data API v3 publishing (config.py, youtube.py) —
            also not a LangGraph stage; see "Upload — YouTube publishing" above
main.py     CLI dispatcher for every stage, the `pipeline` composite, and the
            review/approve/reject/upload/comment/authorize-youtube verbs
output/     generated media (gitignored)
```

## Rate limits designed around

- **Groq free tier:** Orpheus TTS 10 RPM / 100 RPD; whisper-turbo 20 RPM / 2K RPD;
  the research LLM account is capped low on input/output tokens-per-minute, so
  tool results are truncated and per-tool call counts are hard-capped.
- **Tavily** is metered (paid credits), so it's structurally capped to 1 call per
  research run; Wikipedia / arXiv / YouTube search are free and preferred first.
- **Gemini** video generation is the scarcest resource — aggressive scene-level
  caching means reruns only regenerate scenes whose prompt actually changed.

## Status

All 7 stages are built. Stages 1–6 are verified end-to-end (a 6-scene short
renders to `output/shorts/<slug>_<id>.mp4`), with a bounded Script
critic/revise loop and an out-of-band `review`/`approve`/`reject` lifecycle on
top. **Upload** (`upload/`) publishes only `approved` shorts to YouTube and
deletes them locally on success; it's verified offline (approval-gate
enforcement, credential-error paths) — a live first upload needs your own
Cloud Console OAuth client, via `python main.py authorize-youtube`.
