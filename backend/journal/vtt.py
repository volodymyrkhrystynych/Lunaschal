"""WebVTT → plain text, for YouTube's captions.

Pure and dependency-free, because the interesting part is a data-shape problem
rather than an I/O one: **YouTube's auto-generated captions are a rolling
window, not a partition of the transcript.** A typical auto-caption file reads

    00:00:01.000 --> 00:00:03.000
    the first thing they said

    00:00:03.000 --> 00:00:05.000
    the first thing they said
    and then the second thing

    00:00:05.000 --> 00:00:07.000
    and then the second thing
    and then the third

— every line appears twice, once as the incoming line and once as the outgoing
one, so naive concatenation roughly doubles the transcript and makes the model
summarizing it read everything twice. Hence `_dedupe`.

Manually-authored subtitle tracks do partition cleanly, and go through the same
path unharmed: a line is only dropped when it is identical to the line already
emitted before it, which in a partitioned track means a genuinely repeated line
(a chorus, a stutter) — losing one of those costs nothing a summary would miss.
"""
import re

# `00:00:03.000 --> 00:00:05.000 align:start position:0%`, and the WebVTT
# variant with only two clock fields (`00:03.000`).
_TIMING = re.compile(r'^\s*[\d:.]+\s*-->\s*[\d:.]+')

# Inline karaoke markup inside a cue: `<00:00:03.480>` timestamps and the
# `<c>`/`</c>` colour spans YouTube wraps each word in.
_INLINE_TAG = re.compile(r'<[^>]*>')

# `WEBVTT`, plus the block headers that introduce metadata rather than speech.
_BLOCK_HEADERS = ('WEBVTT', 'NOTE', 'STYLE', 'REGION')

# A bare cue identifier: VTT allows a line before the timing line naming the
# cue. YouTube emits integers; the spec allows anything without `-->`. Only
# integer-only ids are dropped, since arbitrary text there is indistinguishable
# from a caption and dropping it would eat real words.
_CUE_ID = re.compile(r'^\d+$')


def _dedupe(lines: list[str]) -> list[str]:
    """Drop each line that repeats the one emitted before it.

    Compared against the last *emitted* line rather than the previous input
    line, so a line carried across three overlapping cues collapses to one copy
    rather than two.
    """
    out: list[str] = []
    for line in lines:
        if out and out[-1] == line:
            continue
        out.append(line)
    return out


def _clean(line: str) -> str:
    text = _INLINE_TAG.sub('', line).strip()
    # &amp; and friends: cheap enough to do here, and a transcript full of
    # `&amp;` is a transcript the model reads as markup.
    return (
        text.replace('&amp;', '&')
        .replace('&lt;', '<')
        .replace('&gt;', '>')
        .replace('&quot;', '"')
        .replace('&#39;', "'")
        .replace('&nbsp;', ' ')
        .strip()
    )


def vtt_to_text(raw: str) -> str:
    """The spoken words in a WebVTT file, one line per distinct caption line.

    Returns '' for an empty file, a header-only file, or anything that turns out
    to hold no caption text — the caller treats that the same as no captions at
    all and falls back to transcribing the audio.
    """
    if not raw:
        return ''
    # A BOM is common on subtitle files and would otherwise hide the WEBVTT
    # header from the startswith check below.
    raw = raw.lstrip('\ufeff')

    # Block-wise rather than line-wise, because both things we need to discard
    # are only recognisable by *position*: `NOTE` introduces a comment only as
    # the first line of a block (a caption may legitimately begin with the word
    # "NOTE"), and a bare integer is a cue identifier only on the line directly
    # above the timing line (a caption may legitimately be the year "2024").
    lines: list[str] = []
    for block in re.split(r'\r?\n\s*\r?\n', raw):
        rows = [r.strip() for r in block.splitlines()]
        rows = [r for r in rows if r]
        if not rows:
            continue
        if rows[0].split(' ', 1)[0] in _BLOCK_HEADERS:
            continue
        timing = next((i for i, r in enumerate(rows) if _TIMING.match(r)), None)
        if timing is None:
            # No timing line: not a cue. Either the file is not VTT at all or
            # this is a stray block; either way there is nothing here we can
            # honestly call speech.
            continue
        # Everything above the timing line is the cue identifier; everything
        # below it is what was said.
        for row in rows[timing + 1:]:
            text = _clean(row)
            if text:
                lines.append(text)

    return '\n'.join(_dedupe(lines)).strip()
