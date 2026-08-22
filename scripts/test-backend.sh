#!/usr/bin/env bash
set -e

cd "$(dirname "$0")/.."

# Runs the backend suite (~1,800 tests) as several smaller pytest sessions
# instead of one giant one.
#
# `tmp_path_retention_policy = failed` (pytest.ini) already frees each
# *passing* test's tmp_path the moment that test ends, which is most of the
# fix for a suite whose autouse `isolated_db` fixture copies a ~1MB DB into
# every test's tmp_path. But a batch with real failures still accumulates
# those failed dirs for the whole batch, and stale dirs can be left behind by
# an interrupted run (Ctrl-C, a segfault under a background-thread test — see
# backend/tests/conftest.py). So on top of the ini fix, this script also
# wipes pytest's own tmp root before each batch, bounding worst-case usage to
# one batch's failures rather than the whole suite's.
#
# A single `pytest <one file>` for quick iteration is unaffected by any of
# this and doesn't need this script.

BATCH_SIZE="${TEST_BATCH_SIZE:-15}"
PYTEST="${PYTEST_BIN:-.venv/bin/pytest}"

TMP_ROOT="$(python3 -c 'import tempfile, getpass; print(f"{tempfile.gettempdir()}/pytest-of-{getpass.getuser()}")')"

mapfile -t files < <(find backend/tests -maxdepth 1 -name 'test_*.py' | sort)

if [ ${#files[@]} -eq 0 ]; then
  echo "No test files found under backend/tests" >&2
  exit 1
fi

failed_batches=0
batch_num=0
total_batches=$(( (${#files[@]} + BATCH_SIZE - 1) / BATCH_SIZE ))

for ((i = 0; i < ${#files[@]}; i += BATCH_SIZE)); do
  batch=("${files[@]:i:BATCH_SIZE}")
  batch_num=$((batch_num + 1))

  echo "== backend test batch $batch_num/$total_batches (${#batch[@]} files) =="
  rm -rf "$TMP_ROOT"

  if ! "$PYTEST" "${batch[@]}"; then
    failed_batches=$((failed_batches + 1))
  fi
done

rm -rf "$TMP_ROOT"

if [ "$failed_batches" -gt 0 ]; then
  echo "$failed_batches/$total_batches batch(es) had failures"
  exit 1
fi

echo "All $total_batches batches passed"
