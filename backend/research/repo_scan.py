"""What any repository is, read rather than summarized.

repo_facts.py knows how to read *this* app: Flask blueprints, the frontend's
three hand-synced view lists, `api.<ns>` in src/hooks/api.ts. That is worth
keeping — those extractors are exact where a paraphrase would drift — but none
of it applies to an arbitrary checkout, and the Ideas tab now holds several.

So this module is the half that works anywhere: the module index, the language
mix, the layout, and the headings of whatever docs the repo maintains. It knows
nothing about Flask or React. repo_facts' specific extractors run on top when
their fingerprints match, and are simply absent when they don't.

The module index is the load-bearing piece. It is what the nightly code-wiki
pass rotates through — "every directory that holds source, how big it is, and
whether it documents itself" is exactly the work list for "write a note about
each part of this codebase, biggest first".

Nothing here reads file *contents* except docs and a line count. Pushing a repo
through a 25 tok/s local model to find out how many Python files it has would
cost tens of thousands of tokens for something `len()` answers exactly.
"""
import re
from collections import defaultdict
from pathlib import Path

from backend.research.repo_facts import _SKIP_DIRS, _read

# Bounded so one pathological repo cannot produce a snapshot too large to fit
# in a prompt.
MAX_MODULES = 120
MAX_FILES_SCANNED = 20000
MAX_DOC_FILES = 12
MAX_HEADINGS = 60

SKIP_DIRS = _SKIP_DIRS | {'graphify-out', '.next', '.turbo', 'target', 'vendor',
                          '.idea', '.vscode', 'coverage', '.tox'}

# What counts as source for the module index. Extensions rather than a language
# detector: a name is all that is needed to group files, and guessing wrong
# about a `.h` costs nothing here.
SOURCE_EXTS = {
    '.py': 'Python', '.ts': 'TypeScript', '.tsx': 'TypeScript', '.js': 'JavaScript',
    '.jsx': 'JavaScript', '.mjs': 'JavaScript', '.cjs': 'JavaScript',
    '.rs': 'Rust', '.go': 'Go', '.java': 'Java', '.kt': 'Kotlin', '.rb': 'Ruby',
    '.php': 'PHP', '.cs': 'C#', '.c': 'C', '.h': 'C', '.cc': 'C++', '.cpp': 'C++',
    '.hpp': 'C++', '.swift': 'Swift', '.m': 'Objective-C', '.scala': 'Scala',
    '.ex': 'Elixir', '.exs': 'Elixir', '.erl': 'Erlang', '.hs': 'Haskell',
    '.lua': 'Lua', '.sh': 'Shell', '.bash': 'Shell', '.sql': 'SQL',
    '.vue': 'Vue', '.svelte': 'Svelte', '.dart': 'Dart', '.zig': 'Zig',
}

# Docs a repository is likely to maintain by hand. Read and indexed, never
# regenerated: they are denser than anything a model would write about the same
# thing, and they say what the author meant rather than what the code does.
_DOC_CANDIDATES = (
    'README.md', 'README.rst', 'CLAUDE.md', 'AGENTS.md', 'CONTRIBUTING.md',
    'ARCHITECTURE.md', 'docs/architecture.md', 'docs/README.md',
    'docs/ROADMAP.md', 'docs/TODO.md', 'ROADMAP.md', 'TODO.md',
)


def is_repo(root: Path | None) -> bool:
    """Any directory with a git checkout in it is scannable.

    Deliberately weaker than repo_facts.is_repo, which fingerprints *this* app.
    An arbitrary repository is still worth scanning; it just yields the generic
    half of the facts.
    """
    return bool(root) and root.is_dir() and (root / '.git').exists()


def _iter_source(root: Path):
    """Every source file under `root`, skipping dependencies and build output.

    Prunes whole directories rather than filtering paths afterwards: walking
    into node_modules and discarding the results is the difference between a
    scan that takes a second and one that takes a minute.
    """
    seen = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name in SKIP_DIRS or entry.name.startswith('.env'):
                continue
            if entry.is_symlink():
                # A symlinked directory can point back up the tree; following
                # one turns the walk into a loop.
                continue
            if entry.is_dir():
                if not entry.name.startswith('.'):
                    stack.append(entry)
                continue
            if entry.suffix.lower() in SOURCE_EXTS:
                seen += 1
                if seen > MAX_FILES_SCANNED:
                    return
                yield entry


def _line_count(path: Path) -> int:
    try:
        with path.open('rb') as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def scan_tree(root: Path) -> dict:
    """One walk, producing both the module index and the language mix.

    They come from the same traversal because the walk is the expensive part
    and doing it twice for two summaries of the same files would be waste.
    """
    modules: dict[str, dict] = defaultdict(
        lambda: {'files': 0, 'lines': 0, 'languages': set()}
    )
    languages: dict[str, dict] = defaultdict(lambda: {'files': 0, 'lines': 0})

    for path in _iter_source(root):
        lines = _line_count(path)
        language = SOURCE_EXTS[path.suffix.lower()]
        rel_dir = str(path.parent.relative_to(root))
        rel_dir = '' if rel_dir == '.' else rel_dir

        module = modules[rel_dir]
        module['files'] += 1
        module['lines'] += lines
        module['languages'].add(language)

        languages[language]['files'] += 1
        languages[language]['lines'] += lines

    module_list = [
        {
            'path': path,
            'files': data['files'],
            'lines': data['lines'],
            'languages': sorted(data['languages']),
        }
        for path, data in modules.items()
    ]
    # Biggest first: it is both the useful reading order for a human and the
    # order the code-wiki pass wants, since the largest undocumented module is
    # the one most worth a note.
    module_list.sort(key=lambda m: (-m['lines'], m['path']))

    language_list = [
        {'language': name, 'files': d['files'], 'lines': d['lines']}
        for name, d in languages.items()
    ]
    language_list.sort(key=lambda entry: -entry['lines'])

    return {
        'modules': module_list[:MAX_MODULES],
        'moduleCount': len(module_list),
        'languages': language_list,
        'fileCount': sum(entry['files'] for entry in language_list),
        'lineCount': sum(entry['lines'] for entry in language_list),
    }


def layout_facts(root: Path) -> list[str]:
    """The top-level entries, so the agent knows where to start looking."""
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name))
    except OSError:
        return []
    out = []
    for entry in entries:
        if entry.name in SKIP_DIRS or entry.name.startswith('.env'):
            continue
        if entry.name.startswith('.') and entry.name != '.github':
            continue
        out.append(f'{entry.name}/' if entry.is_dir() else entry.name)
    return out


def doc_facts(root: Path) -> list[dict]:
    """Headings from whatever docs the repo maintains, roadmap items verbatim.

    Verbatim matters for a roadmap: the assessor compares an idea against what
    is already written down, and a paraphrase of "we plan to add X" is exactly
    the kind of thing that turns into "we added X".
    """
    out: list[dict] = []
    seen: set[str] = set()

    candidates = list(_DOC_CANDIDATES)
    docs_dir = root / 'docs'
    if docs_dir.is_dir():
        try:
            candidates += sorted(
                str(p.relative_to(root)) for p in docs_dir.glob('*.md')
            )
        except OSError:
            pass

    for rel in candidates:
        if rel in seen or len(out) >= MAX_DOC_FILES:
            continue
        seen.add(rel)
        text = _read(root, rel)
        if not text:
            continue
        headings = [
            {'level': len(m.group(1)), 'text': m.group(2).strip()}
            for m in re.finditer(r'^(#{1,4}) +(.+)$', text, re.MULTILINE)
        ][:MAX_HEADINGS]
        entry: dict = {'path': rel, 'headings': headings, 'bytes': len(text)}
        if 'roadmap' in rel.lower() or 'todo' in rel.lower():
            entry['items'] = [
                m.group(1).strip()[:300]
                for m in re.finditer(r'^- \*\*(.+?)\*\*', text, re.MULTILINE)
            ]
        out.append(entry)
    return out


def build_scan(root: Path) -> dict:
    """The generic half of a repo's facts."""
    from backend.research.repo_facts import git_facts
    tree = scan_tree(root)
    return {
        'root': str(root),
        'git': git_facts(root),
        'layout': layout_facts(root),
        'docs': doc_facts(root),
        **tree,
    }


def _plural(count: int, noun: str) -> str:
    return f'{count:,} {noun}' if count == 1 else f'{count:,} {noun}s'


def render_scan(facts: dict, name: str = '') -> str:
    """Markdown for the generic facts. Pure — no DB, no filesystem, no model."""
    title = f'# {name} repository inventory' if name else '# Repository inventory'
    lines: list[str] = [title, '']

    git = facts.get('git') or {}
    if git.get('sha'):
        lines += [f"Commit `{git['sha'][:10]}` on `{git.get('branch') or 'unknown'}`.", '']

    languages = facts.get('languages') or []
    if languages:
        lines += ['## Languages', '', ', '.join(
            f"{entry['language']} ({_plural(entry['files'], 'file')},"
            f" {entry['lines']:,} lines)"
            for entry in languages[:8]
        ), '']

    layout = facts.get('layout') or []
    if layout:
        lines += ['## Layout', '', ', '.join(layout), '']

    modules = facts.get('modules') or []
    if modules:
        lines += ['## Modules, largest first', '']
        for module in modules[:40]:
            path = module['path'] or '(root)'
            lines.append(
                f"- `{path}` — {_plural(module['files'], 'file')},"
                f" {module['lines']:,} lines ({', '.join(module['languages'])})"
            )
        if len(modules) > 40:
            lines.append(f'- … and {facts.get("moduleCount", len(modules)) - 40} more')
        lines.append('')

    for doc in facts.get('docs') or []:
        if doc.get('items'):
            lines += [f"## Already written down in {doc['path']}", '']
            lines += [f'- {item}' for item in doc['items']]
            lines.append('')

    doc_paths = [d['path'] for d in facts.get('docs') or []]
    if doc_paths:
        lines += ['## Docs in this repo', '', ', '.join(doc_paths), '']

    return '\n'.join(lines).rstrip() + '\n'
