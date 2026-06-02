"""
Subtitle utilities: style presets, position CSS, text formatting.
"""

import re
from typing import Dict, Any, List

SUBTITLE_STYLE_CSS: Dict[str, str] = {
    "simple_white": (
        "color: #ffffff; "
        "text-shadow: 0 2px 8px rgba(0,0,0,0.6);"
    ),
    "hk_yellow_red": (
        "color: #FFD21A; "
        "font-weight: 900; "
        "font-family: 'FZZongYi-M05S', 'HYZongYiTi', 'YouSheBiaoTiHei', "
        "'Source Han Sans SC Heavy', 'Noto Sans CJK SC', sans-serif; "
        "-webkit-text-stroke: 3px #B00000; "
        "text-shadow: "
        "0 0 4px #B00000, "
        "0 0 8px #D00000, "
        "0 0 14px rgba(255,0,0,0.85), "
        "3px 3px 0 #5A0000; "
        "letter-spacing: 2px; "
        "line-height: 1.15; "
    ),
    "outlined": (
        "color: #ffffff; "
        "text-shadow: -2px -2px 0 #000, 2px -2px 0 #000, "
        "-2px 2px 0 #000, 2px 2px 0 #000, 0 2px 4px rgba(0,0,0,0.5);"
    ),
    "semi_bg": (
        "color: #ffffff; "
        "background: rgba(0,0,0,0.6); "
        "border-radius: 8px; "
        "padding: 10px 20px; "
        "box-sizing: border-box;"
    ),
    "card": (
        "color: #ffffff; "
        "background: rgba(0,0,0,0.78); "
        "border-radius: 16px; "
        "padding: 20px 28px; "
        "backdrop-filter: blur(12px); "
        "-webkit-backdrop-filter: blur(12px);"
    ),
}

SUBTITLE_POSITION_CSS: Dict[str, str] = {
    "top": "top: 120px;",
    "middle": "top: 50%; transform: translateY(-50%);",
    "bottom": "bottom: 80px;",
}


def build_subtitle_template_vars(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert subtitle config params into template variable dict.
    CSS vars (subtitle_style_css, subtitle_position_css) are injected into the
    HTML template via {{...}} substitution.
    Processing vars (_subtitle_max_lines, _subtitle_chars_per_line) are used
    by frame_processor for Python-side text formatting and are ignored by the
    template renderer.
    """
    style = params.get("subtitle_style", "simple_white")
    position = params.get("subtitle_position", "bottom")
    max_lines = int(params.get("subtitle_max_lines", 2))
    chars_per_line = int(params.get("subtitle_chars_per_line", 20))

    return {
        "subtitle_style_css": SUBTITLE_STYLE_CSS.get(style, SUBTITLE_STYLE_CSS["simple_white"]),
        "subtitle_position_css": SUBTITLE_POSITION_CSS.get(position, SUBTITLE_POSITION_CSS["bottom"]),
        "_subtitle_max_lines": max_lines,
        "_subtitle_chars_per_line": chars_per_line,
    }


def split_narration_into_subtitle_chunks(
    text: str, max_lines: int = 2, chars_per_line: int = 20
) -> List[str]:
    """
    Split a long narration into subtitle-display-sized chunks.
    Each chunk fits within max_lines * chars_per_line characters so that the
    subtitle always matches the TTS audio for that chunk (no truncation).

    Splits at natural boundaries in priority order:
      1. Sentence endings: 。！？!?
      2. Clause endings:   ，；,;
      3. Phrase endings:   、
      4. Force-split at max_chars if no boundary found

    Returns a list of non-empty chunks (at least one element).
    """
    max_chars = max_lines * chars_per_line
    text = " ".join(text.split())  # normalise whitespace
    if not text:
        return [""]
    if len(text) <= max_chars:
        return [text]

    # Split after each punctuation character, keeping the delimiter
    parts = [p for p in re.split(r'(?<=[。！？!?，；,;、])', text) if p]

    chunks: List[str] = []
    current = ""
    for part in parts:
        if len(current) + len(part) <= max_chars:
            current += part
        else:
            if current:
                chunks.append(current)
            if len(part) <= max_chars:
                current = part
            else:
                # Part itself exceeds max_chars — force-split
                while len(part) > max_chars:
                    chunks.append(part[:max_chars])
                    part = part[max_chars:]
                current = part
    if current:
        chunks.append(current)

    return chunks if chunks else [text]


def map_subtitle_chunks_to_timeline(
    text: str,
    chunks: List[str],
    boundaries: List[Dict[str, Any]] | None,
    total_dur: float,
) -> List[tuple]:
    """
    Map subtitle chunks to (t_start, duration) windows WITHIN one continuous
    audio clip.

    The narration is now TTS'd as a single utterance (continuous, natural
    prosody), so subtitle display windows must be derived from timing inside
    that audio rather than from per-chunk audio durations.

    Two strategies:
      • If `boundaries` (Edge TTS WordBoundary timings) are available, anchor each
        chunk to the start time of the first spoken token that falls inside it —
        precise alignment.
      • Otherwise fall back to splitting `total_dur` proportionally to each
        chunk's character length.

    Windows are contiguous and gapless, covering [0, total_dur]; the last chunk
    always extends to total_dur so no trailing frames are left uncovered.

    Returns: list of (t_start, duration) tuples, one per chunk.
    """
    n = len(chunks)
    if n == 0:
        return []
    if n == 1:
        return [(0.0, total_dur)]

    # Char ranges of each chunk inside the normalised narration text.
    norm = " ".join(text.split())
    ranges: List[tuple] = []
    pos = 0
    for ch in chunks:
        start = norm.find(ch, pos)
        if start < 0:
            start = pos
        end = start + len(ch)
        ranges.append((start, end))
        pos = end

    def _proportional() -> List[tuple]:
        lengths = [max(len(c), 1) for c in chunks]
        tot = sum(lengths)
        out, acc = [], 0.0
        for k, L in enumerate(lengths):
            d = total_dur * L / tot
            # absorb rounding into the last chunk
            if k == n - 1:
                d = max(total_dur - acc, 0.0)
            out.append((acc, d))
            acc += d
        return out

    if not boundaries:
        return _proportional()

    # Anchor each spoken token to a char position in the narration text.
    anchors: List[tuple] = []  # (char_pos, start_time)
    cur = 0
    for b in boundaries:
        tok = (b.get("text") or "").strip()
        if not tok:
            continue
        idx = norm.find(tok, cur)
        if idx < 0:
            idx = cur
        anchors.append((idx, float(b.get("start", 0.0))))
        cur = idx + len(tok)

    if not anchors:
        return _proportional()

    # Each chunk start = first anchor at/after the chunk's start char.
    starts = [None] * n
    ai = 0
    for i, (c0, _c1) in enumerate(ranges):
        t = None
        while ai < len(anchors) and anchors[ai][0] < c0:
            ai += 1
        if ai < len(anchors):
            t = anchors[ai][1]
        starts[i] = t

    # First chunk always starts at 0; carry forward any gaps; keep monotonic.
    starts[0] = 0.0
    for i in range(1, n):
        if starts[i] is None:
            starts[i] = starts[i - 1]
    for i in range(1, n):
        if starts[i] < starts[i - 1]:
            starts[i] = starts[i - 1]

    out: List[tuple] = []
    for i in range(n):
        t0 = starts[i]
        t1 = starts[i + 1] if i + 1 < n else total_dur
        if t1 < t0:
            t1 = t0
        out.append((t0, t1 - t0))
    return out


def format_subtitle_text(text: str, max_lines: int = 2, chars_per_line: int = 20) -> str:
    """
    Wrap subtitle text at chars_per_line characters and limit to max_lines.
    Works correctly for CJK (no spaces) and Latin (space-aware) text.
    Returns lines joined by \\n; use CSS white-space: pre-line in the template.
    """
    if not text:
        return text

    # Normalise whitespace to single spaces
    flat = " ".join(text.split())

    lines = []
    while flat and len(lines) < max_lines:
        if len(flat) <= chars_per_line:
            lines.append(flat)
            break
        chunk = flat[:chars_per_line]
        last_space = chunk.rfind(" ")
        if last_space > chars_per_line // 2:
            lines.append(flat[:last_space])
            flat = flat[last_space + 1:]
        else:
            lines.append(flat[:chars_per_line])
            flat = flat[chars_per_line:]

    return "\n".join(lines)
