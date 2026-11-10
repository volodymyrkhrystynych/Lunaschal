"""What a test run's output says went wrong, pulled out before the tail is cut.

The driver keeps a bounded amount of the test command's output, and the end of
it is the least informative part: `scripts/test-backend.sh` runs the suite in
21 batches, so the last 200 lines were the last few batches — all green —
followed by "5/21 batch(es) had failures". Which tests failed had scrolled off
long before. So the failures are found here first, in the whole output, and
put ahead of the tail; they are what the person reviewing the task and the
agent asked to fix it both need.

Pure and DB-free. Knows pytest (plain and batched by test-backend.sh) and
Vitest; anything else still gets its tail.
"""
from __future__ import annotations

import re

BATCH = re.compile(r'^== backend test batch (\d+)/(\d+)')
RUNNER_CLOSING = re.compile(r'batch\(es\) had failures|^All \d+ batches passed')
# pytest's closing line: "==== 1 failed, 20 passed in 3.1s ====".
PYTEST_SUMMARY = re.compile(r'^=+ .* in [\d.]+s( \([^)]*\))? =+$')
PYTEST_BAD_SUMMARY = re.compile(r'\b(failed|errors?)\b')

# Lines that name a failure wherever they appear.
FAILURE_LINE = re.compile(
    r'^(FAILED|ERROR) \S'                       # pytest short summary
    r'|^ ?FAIL\s+\S'                            # vitest: " FAIL  src/x.test.ts > case"
    r'|^\s*[×✗] \S'                             # vitest/tap failing case
    r'|^\s*Test Files .*\bfailed\b'             # vitest totals
    r'|^\s*Tests .*\bfailed\b'
    r'|Fatal Python error|Segmentation fault|core dumped|^Killed$|INTERNALERROR'
    r'|^ERROR: |^npm ERR!|^npm error '
    r'|batch\(es\) had failures'
)

MAX_FAILURE_LINES = 120
MAX_LINE = 300
CRASH_CONTEXT = 6


def _clip(line: str) -> str:
    return line if len(line) <= MAX_LINE else line[:MAX_LINE - 1] + '…'


def failures(text: str) -> list[str]:
    """The lines of `text` that say what failed, in order, deduplicated.

    For a batched backend run each failing batch is introduced by its header,
    and a batch that ended without pytest's closing line — a crash, an abort,
    a kill — is reported with its last few lines, since it has no FAILED
    lines of its own to show.
    """
    lines = text.splitlines()
    out: list[str] = []
    seen: set[str] = set()

    def emit(line: str) -> None:
        line = _clip(line.rstrip())
        if line and line not in seen:
            seen.add(line)
            out.append(line)

    # Split into sections at batch headers; a run with no batches is one section.
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    closing: list[str] = []
    for line in lines:
        if BATCH.match(line):
            sections.append((line.strip(), []))
        elif RUNNER_CLOSING.search(line):
            # The batch runner's own last word belongs to no batch.
            closing.append(line)
        else:
            sections[-1][1].append(line)
    sections.append((None, closing))

    for header, body in sections:
        hits = [l for l in body if FAILURE_LINE.search(l)]
        summaries = [l for l in body if PYTEST_SUMMARY.match(l.strip())]
        bad_summary = [l for l in summaries if PYTEST_BAD_SUMMARY.search(l)]
        crashed = header is not None and not summaries
        if header is not None and not (hits or bad_summary or crashed):
            continue
        if header is not None:
            emit(header)
        for line in hits:
            emit(line)
        for line in bad_summary:
            emit(line.strip())
        if crashed:
            emit('(this batch ended without a pytest summary — it crashed or was killed; '
                 'its last lines:)')
            for line in [l for l in body if l.strip()][-CRASH_CONTEXT:]:
                out.append(_clip('  ' + line.rstrip()))
    return out[:MAX_FAILURE_LINES]


def report(text: str, tail_lines: int = 200) -> str:
    """The failures (if any were recognised) followed by the end of the output."""
    lines = text.splitlines()
    tail = '\n'.join(lines[-tail_lines:])
    found = failures(text)
    if not found:
        return tail
    return 'What failed:\n' + '\n'.join(found) + '\n\n── end of the output ──\n' + tail


def clip_report(text: str, limit: int) -> str:
    """`report` cut to `limit` characters, cutting the tail before the failures."""
    if len(text) <= limit:
        return text
    marker = '\n\n── end of the output ──\n'
    head, sep, tail = text.partition(marker)
    if not sep:
        return text[-limit:]
    if len(head) + len(sep) >= limit:
        return head[:limit - 1] + '…'
    room = limit - len(head) - len(sep)
    return head + sep + tail[-room:]
