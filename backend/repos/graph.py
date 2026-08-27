"""The graphify graph inside a clone.

graphify turns a directory of code into a queryable knowledge graph. Two things
about it decided how it is used here:

**`graphify update <path>` builds a graph from nothing.** There is no separate
build command — `update` bootstraps and incrementally refreshes with the same
call, extracting code structurally by AST with **no LLM and no API key**. So one
command covers both moments that matter: right after the import clone, and after
every nightly pull.

**A graph is an accelerator, never a requirement.** It answers "which files is
this concept in?" in well under a second and zero model tokens, which is a good
first move before reading anything. But it is a node index, not the source, and
a repo without one is fully usable — the code_map tool is simply absent from the
toolbox (backend/research/code.py), the same degrade-by-absence web search uses.
"""
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

BUILD_TIMEOUT = 1800   # a first build on a large repo; measured in seconds normally
QUERY_TIMEOUT = 30

DEFAULT_QUERY_BUDGET = 1200


def graphify_bin() -> str | None:
    """The graphify executable, or None when it isn't installed.

    GRAPHIFY_BIN overrides for tests and for a non-PATH install.
    """
    override = os.environ.get('GRAPHIFY_BIN')
    if override:
        return override if Path(override).exists() else None
    return shutil.which('graphify')


def graph_path(root: Path) -> Path:
    return root / 'graphify-out' / 'graph.json'


def has_graph(root: Path | None) -> bool:
    return bool(root) and graph_path(root).is_file()


def build(root: Path) -> dict | None:
    """Build or refresh the graph in `root`. Returns {nodeCount} or None.

    `--force` on purpose. Without it graphify refuses a rebuild that produces
    fewer nodes than the last one, which is the right default for a human who
    might have mis-scanned — but here the tree was just reset to the remote, so
    it *is* the truth, and a commit that deletes a package should shrink the
    graph rather than leave a stale one standing.

    Never raises: a missing binary or a failed build costs the code_map tool,
    not the import.
    """
    binary = graphify_bin()
    if not binary:
        logger.info('graphify is not installed; skipping graph build for %s', root)
        return None
    try:
        out = subprocess.run(
            [binary, 'update', str(root), '--force'],
            capture_output=True, text=True, timeout=BUILD_TIMEOUT,
            cwd=str(root),
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning('graphify build failed for %s: %s', root, e)
        return None
    if out.returncode != 0:
        logger.warning(
            'graphify build failed for %s: %s', root,
            (out.stderr or out.stdout or '').strip()[:300],
        )
        return None
    return {'nodeCount': node_count(root)}


def node_count(root: Path) -> int | None:
    """How many nodes the graph holds, read from the file itself.

    graphify prints the count, but parsing its stdout would make this depend on
    a log line; the graph is the artefact, so the graph is what gets counted.
    """
    path = graph_path(root)
    if not path.is_file():
        return None
    try:
        with path.open(encoding='utf-8') as fh:
            return len(json.load(fh).get('nodes') or [])
    except (OSError, ValueError):
        return None


def query(root: Path, question: str, budget: int = DEFAULT_QUERY_BUDGET) -> str | None:
    """A BFS traversal of the graph for `question`, as graphify prints it.

    Returns None when there is no graph or no binary — the caller reads that as
    "this tool is unavailable" rather than as an empty result, because the two
    mean different things to a model deciding what to do next.
    """
    binary = graphify_bin()
    if not binary or not has_graph(root):
        return None
    try:
        out = subprocess.run(
            [binary, 'query', question, '--graph', str(graph_path(root)),
             '--budget', str(budget)],
            capture_output=True, text=True, timeout=QUERY_TIMEOUT, cwd=str(root),
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning('graphify query failed: %s', e)
        return None
    if out.returncode != 0:
        return None
    return (out.stdout or '').strip() or None
