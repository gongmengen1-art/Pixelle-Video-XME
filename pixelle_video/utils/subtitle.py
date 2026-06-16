"""
Subtitle utilities: style presets, position CSS, text formatting.
"""

import html as _html
import re
from typing import Dict, Any, List, Tuple

# ── Emphasis (重点标记) ──────────────────────────────────────────────────────
# Authors mark emphasised words in the custom script with [[ ]], e.g.
#   "今天教大家一个[[超实用]]的小技巧。"
# The markers are stripped before TTS / chunking / alignment (so speech and
# timing are unaffected) and the wrapped characters are rendered with a
# distinct colour + larger size + bold stroke in the subtitle PNG.
EMPHASIS_OPEN = "[["
EMPHASIS_CLOSE = "]]"
DEFAULT_EMPHASIS_COLOR = "#FFE000"

# Punctuation removed from the *displayed* subtitle only (kept for TTS so
# prosody/pauses stay natural). Scope: sentence-end / pause marks. Quotes,
# book-title marks, dashes and ellipsis are intentionally preserved.
_DISPLAY_STRIP_CJK = set("。．！？，、；：")
_DISPLAY_STRIP_ASCII = set(".,;:!?")

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
    emphasis_color = params.get("subtitle_emphasis_color") or DEFAULT_EMPHASIS_COLOR

    return {
        "subtitle_style_css": SUBTITLE_STYLE_CSS.get(style, SUBTITLE_STYLE_CSS["simple_white"]),
        "subtitle_position_css": SUBTITLE_POSITION_CSS.get(position, SUBTITLE_POSITION_CSS["bottom"]),
        "_subtitle_max_lines": max_lines,
        "_subtitle_chars_per_line": chars_per_line,
        "_subtitle_emphasis_color": emphasis_color,
    }


def parse_emphasis(raw: str) -> Tuple[str, List[bool]]:
    """
    Strip [[ ]] emphasis markers from a narration paragraph and report which
    characters were emphasised.

    Returns (clean_text, flags) where:
      • clean_text  — markers removed AND whitespace collapsed to single spaces
        (identical to " ".join(stripped.split())), so it lines up exactly with
        the `norm` text used by the chunking / timeline-alignment helpers.
      • flags       — list[bool] parallel to clean_text; flags[i] is True when
        clean_text[i] fell inside a [[ ]] pair.

    Unmatched/standalone brackets are treated as literal text. Nested markers
    just keep the depth > 0, so the inner text stays emphasised.
    """
    chars: List[str] = []
    flags: List[bool] = []
    depth = 0
    prev_space = False
    i, n = 0, len(raw)
    while i < n:
        if raw.startswith(EMPHASIS_OPEN, i):
            depth += 1
            i += 2
            continue
        if depth > 0 and raw.startswith(EMPHASIS_CLOSE, i):
            depth -= 1
            i += 2
            continue
        ch = raw[i]
        i += 1
        if ch.isspace():
            # Collapse runs to a single space; drop leading space.
            if chars and not prev_space:
                chars.append(" ")
                flags.append(False)
                prev_space = True
            continue
        prev_space = False
        chars.append(ch)
        flags.append(depth > 0)
    # Trim trailing space introduced by collapsing.
    while chars and chars[-1] == " ":
        chars.pop()
        flags.pop()
    return "".join(chars), flags


def _strip_display_punct(text: str, flags: List[bool]) -> Tuple[List[str], List[bool]]:
    """
    Remove sentence-end / pause punctuation from display text, keeping the
    emphasis flag of every surviving character aligned. ASCII '.' and ',' that
    sit between digits (decimals / thousands separators) are preserved.
    Consecutive / trailing spaces left behind by removal are collapsed.
    """
    out_c: List[str] = []
    out_f: List[bool] = []
    n = len(text)
    for idx, ch in enumerate(text):
        if ch in _DISPLAY_STRIP_CJK:
            continue
        if ch in _DISPLAY_STRIP_ASCII:
            prev = text[idx - 1] if idx > 0 else ""
            nxt = text[idx + 1] if idx + 1 < n else ""
            if ch in ".," and prev.isdigit() and nxt.isdigit():
                pass  # keep decimal / thousands separator
            else:
                continue
        out_c.append(ch)
        out_f.append(flags[idx] if idx < len(flags) else False)

    # Collapse spaces created by stripping (e.g. "Hello , world" → "Hello world").
    c2: List[str] = []
    f2: List[bool] = []
    prev_space = False
    for ch, f in zip(out_c, out_f):
        if ch == " ":
            if prev_space or not c2:
                continue
            prev_space = True
        else:
            prev_space = False
        c2.append(ch)
        f2.append(f)
    while c2 and c2[-1] == " ":
        c2.pop()
        f2.pop()
    return c2, f2


def build_emphasis_css(color: str = DEFAULT_EMPHASIS_COLOR) -> str:
    """Inline CSS for an emphasised run: colour (configurable) + larger + bold."""
    color = color or DEFAULT_EMPHASIS_COLOR
    return (
        f"color:{color};"
        "font-size:1.25em;"
        "font-weight:900;"
        "-webkit-text-stroke:1.5px rgba(0,0,0,0.55);"
        "padding:0 1px;"
    )


def _wrap_flagged(
    chars: List[str], flags: List[bool], max_lines: int, chars_per_line: int
) -> List[Tuple[List[str], List[bool]]]:
    """
    Wrap a flagged char list into <= max_lines lines of <= chars_per_line each,
    breaking at spaces when possible (Latin) and hard-wrapping otherwise (CJK).
    Mirrors format_subtitle_text's wrapping so layout is unchanged. Overflow
    beyond max_lines is dropped (chunks are pre-sized to fit).
    """
    lines: List[Tuple[List[str], List[bool]]] = []
    i, n = 0, len(chars)
    while i < n and len(lines) < max_lines:
        if n - i <= chars_per_line:
            lines.append((chars[i:], flags[i:]))
            break
        window = chars[i:i + chars_per_line]
        last_space = -1
        for k in range(len(window) - 1, -1, -1):
            if window[k] == " ":
                last_space = k
                break
        if last_space > chars_per_line // 2:
            lines.append((chars[i:i + last_space], flags[i:i + last_space]))
            i += last_space + 1  # skip the breaking space
        else:
            lines.append((chars[i:i + chars_per_line], flags[i:i + chars_per_line]))
            i += chars_per_line
    return lines


def _line_to_html(chars: List[str], flags: List[bool], emph_css: str) -> str:
    """Render one line: consecutive emphasised chars are wrapped in a styled span."""
    parts: List[str] = []
    k, n = 0, len(chars)
    while k < n:
        f = flags[k]
        j = k
        while j < n and flags[j] == f:
            j += 1
        seg = _html.escape("".join(chars[k:j]))
        parts.append(f'<span style="{emph_css}">{seg}</span>' if f else seg)
        k = j
    return "".join(parts)


def build_subtitle_inner_html(
    text: str,
    flags: List[bool],
    emphasis_color: str = DEFAULT_EMPHASIS_COLOR,
    max_lines: int = 2,
    chars_per_line: int = 20,
) -> str:
    """
    Build the inner HTML for one subtitle chunk's <div class="text"> slot:
      1. strip display punctuation (keeping emphasis flags aligned),
      2. wrap into lines,
      3. emit HTML-escaped text with emphasised runs wrapped in styled <span>s,
         lines joined by '\\n' (template uses white-space: pre-line).

    `flags` must be parallel to `text` (same length); pass all-False (or a short
    list) when there is no emphasis.
    """
    if flags is None:
        flags = []
    if len(flags) < len(text):
        flags = list(flags) + [False] * (len(text) - len(flags))
    chars, fl = _strip_display_punct(text, flags)
    lines = _wrap_flagged(chars, fl, max_lines, chars_per_line)
    emph_css = build_emphasis_css(emphasis_color)
    return "\n".join(_line_to_html(lc, lf, emph_css) for lc, lf in lines)


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
