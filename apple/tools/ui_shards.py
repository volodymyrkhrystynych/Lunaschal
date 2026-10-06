"""Split the UI tests into shards, so CI can run them on parallel simulators.

Usage: ui_shards.py UITESTS_DIR SHARD TOTAL  (SHARD is 1-based)
Prints one -only-testing argument per line for that shard. Tests are found by
name, so a new test joins a shard on its own; round-robin over sorted names
keeps the slow calendar tests from all landing in one shard.
"""
import pathlib
import re
import sys

TARGET = "LunaschalUITests"


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


def shard(names, index, total):
    if not 1 <= index <= total:
        raise ValueError("Shard must be between 1 and the total")
    return [name for position, name in enumerate(names) if position % total == index - 1]


if __name__ == "__main__":
    directory, index, total = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    chosen = shard(tests(directory), index, total)
    if not chosen:
        raise SystemExit("No UI tests in this shard")
    print("\n".join(f"-only-testing:{TARGET}/{name}" for name in chosen))
