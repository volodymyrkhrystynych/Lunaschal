"""The graphify graph inside a clone.

graphify is an accelerator, not a dependency: the tests that matter are the ones
proving a missing binary or a failed build costs nothing. The one test that
exercises the real thing is skipped when graphify is not installed.
"""
import shutil
from pathlib import Path

import pytest

from backend.repos import graph

pytestmark = pytest.mark.usefixtures('isolated_db')


@pytest.fixture
def code_dir(tmp_path):
    root = tmp_path / 'repo'
    (root / 'pkg').mkdir(parents=True)
    (root / 'pkg' / 'a.py').write_text(
        'def helper(x):\n    return x + 1\n\n\nclass Thing:\n    def run(self):\n'
        '        return helper(2)\n'
    )
    (root / 'pkg' / 'b.py').write_text(
        'from pkg.a import Thing\n\n\ndef main():\n    return Thing().run()\n'
    )
    return root


def test_no_binary_means_no_graph_and_no_exception(code_dir, monkeypatch, tmp_path):
    monkeypatch.setenv('GRAPHIFY_BIN', str(tmp_path / 'absent'))
    assert graph.graphify_bin() is None
    assert graph.build(code_dir) is None
    assert graph.query(code_dir, 'anything') is None
    assert graph.has_graph(code_dir) is False


def test_a_failing_binary_is_swallowed(code_dir, monkeypatch, tmp_path):
    fake = tmp_path / 'graphify'
    fake.write_text('#!/bin/sh\necho "boom" >&2\nexit 1\n')
    fake.chmod(0o755)
    monkeypatch.setenv('GRAPHIFY_BIN', str(fake))
    assert graph.build(code_dir) is None


def test_node_count_survives_a_corrupt_graph(code_dir):
    (code_dir / 'graphify-out').mkdir()
    (code_dir / 'graphify-out' / 'graph.json').write_text('{not json')
    assert graph.node_count(code_dir) is None


def test_query_needs_a_graph_even_with_a_binary(code_dir, monkeypatch, tmp_path):
    fake = tmp_path / 'graphify'
    fake.write_text('#!/bin/sh\necho should-not-run\n')
    fake.chmod(0o755)
    monkeypatch.setenv('GRAPHIFY_BIN', str(fake))
    assert graph.query(code_dir, 'helper') is None


@pytest.mark.skipif(shutil.which('graphify') is None, reason='graphify not installed')
def test_real_graphify_builds_from_nothing_and_answers(code_dir, monkeypatch):
    """`graphify update` bootstraps a graph with no prior graphify-out/ and no
    LLM — which is what lets the import job build one unattended."""
    monkeypatch.delenv('GRAPHIFY_BIN', raising=False)
    assert not graph.has_graph(code_dir)

    built = graph.build(code_dir)
    assert built and built['nodeCount'] > 0
    assert graph.has_graph(code_dir)

    answer = graph.query(code_dir, 'helper thing run', budget=400)
    assert answer and 'pkg/a.py' in answer
