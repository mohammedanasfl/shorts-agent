"""Karaoke caption renderer -- the piece this ffmpeg build can't do itself
(no libass/freetype). Given a scene's word-level timings, it renders one
full-frame transparent PNG per word: the surrounding short phrase is shown
with the currently-spoken word highlighted. mux/encoder.py then overlays each
PNG onto the video for its word's time window, producing the word-by-word pop.

Pure Pillow + stdlib, side-effect-free apart from writing the PNGs it's asked
to write, so the layout logic is easy to reason about without ffmpeg.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from mux.config import (
    CAPTION_BASELINE_Y,
    CAPTION_LINE_SPACING,
    CAPTION_MARGIN_X,
    CAPTION_MAX_WORDS_PER_LINE,
    COLOR_ACTIVE,
    COLOR_NORMAL,
    COLOR_OUTLINE,
    FONT_PATH,
    FONT_SIZE,
    HEIGHT,
    OUTLINE_WIDTH,
    WIDTH,
)

_font = None


def _get_font() -> ImageFont.FreeTypeFont:
    global _font
    if _font is None:
        _font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    return _font


def _chunk_words(words: list[dict]) -> list[list[int]]:
    """Groups consecutive word indices into short phrases: at most
    CAPTION_MAX_WORDS_PER_LINE words, and always breaking after a word that
    ends a sentence so a phrase never straddles a full stop."""
    chunks, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        text = w["word"].strip()
        ends_sentence = text[-1:] in ".!?"
        if len(cur) >= CAPTION_MAX_WORDS_PER_LINE or ends_sentence:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def _wrap(texts: list[str], font, max_w: float) -> list[list[int]]:
    """Greedily wraps a phrase's words into lines that each fit max_w px.
    Returns lines as lists of indices into `texts`."""
    space_w = font.getlength(" ")
    lines, cur, cur_w = [], [], 0.0
    for idx, t in enumerate(texts):
        wlen = font.getlength(t)
        candidate = wlen if not cur else cur_w + space_w + wlen
        if cur and candidate > max_w:
            lines.append(cur)
            cur, cur_w = [idx], wlen
        else:
            cur.append(idx)
            cur_w = candidate
    if cur:
        lines.append(cur)
    return lines


def _render_phrase(texts: list[str], active_local: int, dest: Path) -> None:
    """Renders one full-frame PNG: the phrase `texts`, centered and wrapped,
    with word `active_local` highlighted. Everything else uses the normal
    color; all words get a black outline for legibility over any footage."""
    font = _get_font()
    space_w = font.getlength(" ")
    max_w = WIDTH - 2 * CAPTION_MARGIN_X
    lines = _wrap(texts, font, max_w)

    ascent, descent = font.getmetrics()
    line_h = ascent + descent
    block_h = len(lines) * line_h + (len(lines) - 1) * CAPTION_LINE_SPACING
    # Vertically center the phrase block on the configured baseline anchor.
    y = CAPTION_BASELINE_Y - block_h // 2

    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    for line in lines:
        widths = [font.getlength(texts[i]) for i in line]
        total = sum(widths) + space_w * (len(line) - 1)
        x = (WIDTH - total) / 2
        for i, wlen in zip(line, widths):
            color = COLOR_ACTIVE if i == active_local else COLOR_NORMAL
            draw.text(
                (x, y), texts[i], font=font, fill=color,
                stroke_width=OUTLINE_WIDTH, stroke_fill=COLOR_OUTLINE,
            )
            x += wlen + space_w
        y += line_h + CAPTION_LINE_SPACING

    img.save(dest)


def render_scene_captions(words: list[dict], out_dir: Path, scene_dur: float) -> list[dict]:
    """Renders the whole scene's karaoke frames. Returns an ordered list of
    {"path", "start", "end"} -- one entry per word, each timed to persist from
    its word's onset until the next word begins (the first frame starts at 0 so
    a caption is on screen immediately; the last runs to the scene's end). An
    empty word list (e.g. a scene whose caption was skipped) yields no frames,
    and the scene is muxed video+audio only."""
    if not words:
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    chunks = _chunk_words(words)
    # Map each global word index -> (its chunk's texts, its position in that chunk).
    index_to_phrase = {}
    for chunk in chunks:
        texts = [words[i]["word"].strip() for i in chunk]
        for local, gi in enumerate(chunk):
            index_to_phrase[gi] = (texts, local)

    frames = []
    n = len(words)
    for i in range(n):
        texts, local = index_to_phrase[i]
        dest = out_dir / f"cap_{i:03d}.png"
        _render_phrase(texts, local, dest)
        start = 0.0 if i == 0 else float(words[i]["start"])
        end = float(words[i + 1]["start"]) if i + 1 < n else scene_dur
        # Guard against out-of-order or zero-length windows from the STT timings.
        if end <= start:
            end = start + 0.05
        frames.append({"path": str(dest), "start": round(start, 3), "end": round(end, 3)})
    return frames
