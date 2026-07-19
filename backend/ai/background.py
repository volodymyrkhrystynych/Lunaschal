from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ai-bg')


def run_bg(fn) -> None:
    _executor.submit(fn)
