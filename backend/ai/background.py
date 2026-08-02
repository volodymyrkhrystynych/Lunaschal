"""The shared single-worker queue for deferred AI work.

One FIFO worker, shared by journal polish, journal metadata, attachment
transcription, food structuring, workout parsing and learning-attempt grading.
Everything here was triggered by something the user did seconds ago, so it is
interactive in spirit even though it runs off-request — hence the priority mark
below, which keeps the Ideas research worker from competing with it for a llama
slot.

Long agent runs deliberately do NOT go here: they would head-of-line block
every one of the flows above for minutes. They get their own executor in
backend/research/worker.py.
"""
from concurrent.futures import ThreadPoolExecutor

from backend.ai import priority

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ai-bg')


def run_bg(fn) -> None:
    def _marked():
        with priority.interactive('bg'):
            fn()

    _executor.submit(_marked)
