import json
import pathlib
import subprocess
import sys

import pytest

from scripts.backend_shards import (
    DEFAULT_SECONDS,
    DURATIONS,
    ROOT,
    TESTS,
    shard,
    test_files as find_test_files,
)

SCRIPT = ROOT / 'scripts' / 'backend_shards.py'


def test_finds_test_modules_at_any_depth_and_nothing_else(tmp_path):
    (tmp_path / 'nested').mkdir()
    for name in ['test_a.py', 'nested/test_b.py', 'conftest.py', 'helper.py']:
        (tmp_path / name).write_text('')

    assert find_test_files(tmp_path) == ['nested/test_b.py', 'test_a.py']


def test_every_real_file_lands_in_exactly_one_shard_and_the_load_is_even():
    names = find_test_files(TESTS)
    durations = json.loads(DURATIONS.read_text())
    shards = [shard(names, index, 6, durations) for index in range(1, 7)]

    assert sorted(sum(shards, [])) == names
    loads = [sum(durations.get(name, DEFAULT_SECONDS) for name in group) for group in shards]
    assert max(loads) - min(loads) < max(durations.values())


def test_slow_files_are_spread_even_when_their_names_would_cluster():
    names = [f'test_{i}.py' for i in range(6)]
    durations = dict(zip(names, [60, 1, 1, 60, 1, 1]))
    groups = [shard(names, i, 2, durations) for i in (1, 2)]

    assert not any(names[0] in group and names[3] in group for group in groups)


def test_new_files_get_default_weight_and_removed_files_are_ignored():
    names = ['test_known.py', 'test_new.py']
    durations = {'test_known.py': DEFAULT_SECONDS, 'test_removed.py': 9999}
    groups = [shard(names, i, 2, durations) for i in (1, 2)]

    assert sorted(sum(groups, [])) == names
    assert all(groups)


def test_assignment_does_not_depend_on_discovery_order():
    names = find_test_files(TESTS)
    durations = json.loads(DURATIONS.read_text())

    assert shard(names, 2, 6, durations) == shard(list(reversed(names)), 2, 6, durations)


def test_rejects_a_shard_outside_the_total():
    with pytest.raises(ValueError):
        shard(['test_a.py'], 4, 3)


def test_cli_prints_paths_pytest_can_open():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), '1', '6'], capture_output=True, text=True, check=True,
    )

    assert result.stdout.splitlines()
    assert all((ROOT / line).is_file() for line in result.stdout.splitlines())


def test_cli_fails_rather_than_printing_nothing():
    # pytest given no paths falls back to testpaths and runs the whole suite in
    # every shard, so an empty selection has to stop the job.
    result = subprocess.run(
        [sys.executable, str(SCRIPT), '7', '6'], capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert result.stdout == ''
