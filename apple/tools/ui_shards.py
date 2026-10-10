"""Split the UI tests into shards, so CI can run them on parallel simulators.

Usage: ui_shards.py UITESTS_DIR SHARD TOTAL  (SHARD is 1-based)
Prints one -only-testing argument per line for that shard. Tests are found by
name, so a new test joins a shard on its own. Longest tests are assigned first
to the lightest shard using recorded iPhone durations; new tests get a
one-minute estimate. Reserve a minute in shard 1 for app tests and their launch.
"""
import json
import pathlib
import re
import sys

TARGET = "LunaschalUITests"
DEFAULT_SECONDS = 60
APP_TEST_SECONDS = 60
DURATIONS = pathlib.Path(__file__).with_name("ui_test_durations.json")


def tests(directory):
    found = []
    for path in sorted(pathlib.Path(directory).glob("*.swift")):
        current = None
        for line in path.read_text().splitlines():
            declared = re.match(r"\s*(?:final\s+)?class\s+(\w+)\s*:\s*XCTestCase\b", line)
            if declared:
                current = declared.group(1)
                continue
            test = re.match(r"\s*func\s+(test\w*)\s*\(\s*\)", line)
            if test and current:
                found.append(f"{current}/{test.group(1)}")
    return sorted(found)


def shard(names, index, total, durations=None):
    if not 1 <= index <= total:
        raise ValueError("Shard must be between 1 and the total")
    durations = durations if durations is not None else {}
    groups = [[] for _ in range(total)]
    loads = [APP_TEST_SECONDS] + [0] * (total - 1)
    for name in sorted(names, key=lambda name: (-durations.get(name, DEFAULT_SECONDS), name)):
        lightest = min(range(total), key=lambda i: (loads[i], len(groups[i]), i))
        groups[lightest].append(name)
        loads[lightest] += durations.get(name, DEFAULT_SECONDS)
    return sorted(groups[index - 1])


if __name__ == "__main__":
    directory, index, total = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    chosen = shard(tests(directory), index, total, json.loads(DURATIONS.read_text()))
    if not chosen:
        raise SystemExit("No UI tests in this shard")
    print("\n".join(f"-only-testing:{TARGET}/{name}" for name in chosen))
