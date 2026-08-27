"""Scanning a repository that is not this one.

repo_facts knows how to read a Flask + React app. Everything here has to work
on a checkout that is neither — a Rust crate, someone's Go service — and yield
the generic half without anything having to detect that.

The module index is the piece that matters most: it is the work list the
nightly code-wiki pass rotates through.
"""
import subprocess

import pytest

from backend.research import repo_facts, repo_scan

pytestmark = pytest.mark.usefixtures('isolated_db')

_ENV = {'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@e', 'GIT_COMMITTER_NAME': 'T',
        'GIT_COMMITTER_EMAIL': 't@e', 'PATH': '/usr/bin:/bin'}


@pytest.fixture
def rust_repo(tmp_path):
    """A repository with nothing this app would recognise."""
    root = tmp_path / 'crate'
    (root / 'src' / 'net').mkdir(parents=True)
    (root / 'docs').mkdir()
    (root / 'target' / 'debug').mkdir(parents=True)
    (root / 'node_modules' / 'junk').mkdir(parents=True)

    (root / 'src' / 'main.rs').write_text('fn main() {}\n' * 20)
    (root / 'src' / 'lib.rs').write_text('pub mod net;\n' * 5)
    (root / 'src' / 'net' / 'client.rs').write_text('pub struct Client;\n' * 60)
    (root / 'README.md').write_text('# A crate\n\n## Usage\n\n## Design\n')
    (root / 'docs' / 'ROADMAP.md').write_text(
        '# Roadmap\n\n- **Retry with backoff** — not started\n- **TLS pinning**\n')
    (root / 'Cargo.toml').write_text('[package]\nname = "crate"\n')
    # Both must be invisible: one is build output, one is dependencies.
    (root / 'target' / 'debug' / 'build.rs').write_text('x' * 100)
    (root / 'node_modules' / 'junk' / 'index.js').write_text('x\n' * 900)

    subprocess.run(['git', 'init', '-q', '-b', 'main', str(root)], check=True,
                   env={**_ENV, 'HOME': str(tmp_path)})
    subprocess.run(['git', 'add', '-A'], cwd=root, check=True,
                   env={**_ENV, 'HOME': str(tmp_path)})
    subprocess.run(['git', 'commit', '-qm', 'first'], cwd=root, check=True,
                   env={**_ENV, 'HOME': str(tmp_path)})
    return root


# --- The module index ---

def test_modules_are_listed_largest_first(rust_repo):
    scan = repo_scan.scan_tree(rust_repo)
    paths = [m['path'] for m in scan['modules']]
    assert paths == ['src/net', 'src']
    assert scan['modules'][0]['files'] == 1
    assert scan['modules'][0]['languages'] == ['Rust']


def test_dependencies_and_build_output_are_pruned(rust_repo):
    """Pruned, not filtered afterwards: walking node_modules and discarding
    the results is the difference between a one-second scan and a minute."""
    scan = repo_scan.scan_tree(rust_repo)
    assert not any('node_modules' in m['path'] for m in scan['modules'])
    assert not any('target' in m['path'] for m in scan['modules'])
    assert 'JavaScript' not in [entry['language'] for entry in scan['languages']]


def test_the_language_mix_is_counted_not_guessed(rust_repo):
    scan = repo_scan.scan_tree(rust_repo)
    assert [entry['language'] for entry in scan['languages']] == ['Rust']
    assert scan['fileCount'] == 3
    assert scan['lineCount'] == 85


def test_a_symlinked_directory_does_not_loop_the_walk(rust_repo):
    (rust_repo / 'src' / 'loop').symlink_to(rust_repo)
    scan = repo_scan.scan_tree(rust_repo)
    assert scan['fileCount'] == 3


def test_the_walk_is_bounded(rust_repo, monkeypatch):
    monkeypatch.setattr(repo_scan, 'MAX_FILES_SCANNED', 2)
    assert repo_scan.scan_tree(rust_repo)['fileCount'] == 2


def test_root_level_source_gets_an_empty_module_path(tmp_path):
    root = tmp_path / 'flat'
    root.mkdir()
    (root / 'main.go').write_text('package main\n')
    scan = repo_scan.scan_tree(root)
    assert scan['modules'][0]['path'] == ''


# --- Docs ---

def test_docs_are_found_without_knowing_this_apps_layout(rust_repo):
    docs = repo_scan.doc_facts(rust_repo)
    paths = [d['path'] for d in docs]
    assert 'README.md' in paths and 'docs/ROADMAP.md' in paths
    readme = next(d for d in docs if d['path'] == 'README.md')
    assert [h['text'] for h in readme['headings']] == ['A crate', 'Usage', 'Design']


def test_roadmap_items_are_kept_verbatim(rust_repo):
    """The assessor compares an idea against what is already written down, and
    a paraphrase of "we plan to add X" is how that turns into "we added X"."""
    roadmap = next(d for d in repo_scan.doc_facts(rust_repo)
                   if d['path'] == 'docs/ROADMAP.md')
    assert roadmap['items'] == ['Retry with backoff', 'TLS pinning']


def test_a_repo_with_no_docs_is_fine(tmp_path):
    root = tmp_path / 'bare'
    root.mkdir()
    (root / 'main.py').write_text('x = 1\n')
    assert repo_scan.doc_facts(root) == []


# --- Layout and rendering ---

def test_layout_lists_the_top_level_without_the_noise(rust_repo):
    layout = repo_scan.layout_facts(rust_repo)
    assert 'src/' in layout and 'Cargo.toml' in layout
    assert 'node_modules/' not in layout
    assert '.git/' not in layout


def test_the_rendered_scan_is_pure_markdown(rust_repo):
    facts = repo_scan.build_scan(rust_repo)
    out = repo_scan.render_scan(facts, 'Crate')
    assert out.startswith('# Crate repository inventory')
    assert 'Rust (3 files' in out
    assert '`src/net`' in out
    assert 'Retry with backoff' in out


def test_one_file_is_not_one_files(rust_repo):
    out = repo_scan.render_scan(repo_scan.build_scan(rust_repo))
    assert '1 files' not in out


def test_is_repo_accepts_any_checkout(rust_repo, tmp_path):
    """Deliberately weaker than repo_facts.is_repo, which fingerprints this
    app: an arbitrary repository is still worth scanning."""
    assert repo_scan.is_repo(rust_repo)
    assert not repo_facts.is_repo(rust_repo)
    assert not repo_scan.is_repo(tmp_path / 'nope')


# --- build_facts on a foreign repo ---

def test_build_facts_yields_the_generic_half_and_no_more(rust_repo):
    facts = repo_facts.build_facts(rust_repo, None, live_db=False)
    assert facts['modules'] and facts['languages']
    # Each specific extractor returns [] when its directory is absent, so a
    # Rust repo needs no detection step of its own.
    assert facts['routes'] == []
    assert facts['components'] == []
    assert facts['api'] == []
    assert facts['settings'] == []
    assert facts['tables'] == []


def test_the_digest_of_a_foreign_repo_still_reads(rust_repo):
    facts = repo_facts.build_facts(rust_repo, None, live_db=False)
    digest = repo_facts.render_digest(facts, 'Crate')
    assert digest.startswith('# Crate repo inventory')
    assert '## Modules, largest first' in digest
    assert '## HTTP routes' not in digest


# --- The schema-file fallback ---

def test_tables_come_from_the_schema_file_when_there_is_no_live_db(tmp_path):
    root = tmp_path / 'app'
    (root / 'backend' / 'db').mkdir(parents=True)
    (root / 'backend' / 'db' / 'schema.sql').write_text("""
CREATE TABLE IF NOT EXISTS widgets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    -- a comment that mentions CREATE TABLE fakes
    kind TEXT NOT NULL DEFAULT 'a'
        CHECK(kind IN ('a','b','c')),
    owner_id TEXT REFERENCES people(id) ON DELETE CASCADE,
    UNIQUE(name, kind)
);

CREATE TABLE people (
    id TEXT PRIMARY KEY,
    email TEXT
);
""")
    tables = repo_facts.table_facts_from_sql(root)
    assert [t['table'] for t in tables] == ['people', 'widgets']

    widgets = next(t for t in tables if t['table'] == 'widgets')
    # A wrapped CHECK is a continuation, not a column called "CHECK(kind"; a
    # table-level UNIQUE(a, b) is a constraint, not a column called "UNIQUE(name,".
    assert [c['name'] for c in widgets['columns']] == [
        'id', 'name', 'kind', 'owner_id']
    assert widgets['columns'][0]['pk'] is True
    assert widgets['columns'][1]['notnull'] is True


def test_a_repo_with_no_schema_file_reports_no_tables(rust_repo):
    assert repo_facts.table_facts_from_sql(rust_repo) == []


def test_the_digest_admits_when_tables_were_parsed_rather_than_read(tmp_path):
    """A column added by a migration is missing from a parse, and a missing
    column is otherwise indistinguishable from one that does not exist — which
    is the difference between "add this" and "this is already there"."""
    facts = {'tables': [{'table': 't', 'columns': [{'name': 'id'}]}], 'liveDb': False}
    digest = repo_facts.render_digest(facts)
    assert 'Parsed from the schema file' in digest

    facts['liveDb'] = True
    assert 'Parsed from the schema file' not in repo_facts.render_digest(facts)
