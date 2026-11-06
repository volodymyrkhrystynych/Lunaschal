"""Split backend/tests into shards, so CI can run them as parallel jobs.

Usage: backend_shards.py SHARD TOTAL  (SHARD is 1-based)
Prints one test file per line for that shard. Files are found by name, so a new
one joins a shard on its own. Longest files are assigned first to the lightest
shard using recorded durations; new files get a two-second estimate, about the
average, and removed ones are ignored. Files, not tests: a module's fixtures and
imports stay in one process, and no file is slow enough to unbalance a shard.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS = ROOT / "backend" / "tests"
DEFAULT_SECONDS = 2
DURATIONS = pathlib.Path(__file__).with_name("backend_test_durations.json")


def test_files(directory):
    root = pathlib.Path(directory)
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("test_*.py"))


def shard(names, index, total, durations=None):
    if not 1 <= index <= total:
        raise ValueError("Shard must be between 1 and the total")
    durations = durations if durations is not None else {}
    groups = [[] for _ in range(total)]
    loads = [0] * total
    for name in sorted(names, key=lambda name: (-durations.get(name, DEFAULT_SECONDS), name)):
        lightest = min(range(total), key=lambda i: (loads[i], len(groups[i]), i))
        groups[lightest].append(name)
        loads[lightest] += durations.get(name, DEFAULT_SECONDS)
    return sorted(groups[index - 1])


if __name__ == "__main__":
    index, total = int(sys.argv[1]), int(sys.argv[2])
    chosen = shard(test_files(TESTS), index, total, json.loads(DURATIONS.read_text()))
    if not chosen:
        raise SystemExit("No test files in this shard")
    print("\n".join(f"backend/tests/{name}" for name in chosen))
