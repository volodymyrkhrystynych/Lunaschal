"""Deterministic extraction of what Lunaschal actually is.

This module is the reason the Ideas agent can say "you already built this"
instead of guessing. Every fact here is *read*, never summarized: routes come
from an `ast` walk of the decorators, tables from `PRAGMA table_info` on the
live DB, views from the four hand-synced literals in the frontend. Pushing the
repo through a 25 tok/s local model nightly would cost tens of thousands of
tokens to produce a lossy, drifting paraphrase of things we can extract exactly
in about a second — and "is there already a `paper_pages` table?" is precisely
the kind of question a summary gets wrong.

The LLM's only job (backend/research/repo_job.py) is to summarize the *delta*
since the previous snapshot.

**These extractors are specific to this app**, and say so by returning nothing
when their fingerprints are absent. backend/research/repo_scan.py is the half
that works on any checkout; the two are combined in `build_facts`.

One capability is genuinely weaker for a registered repository than for this
one, and it is worth knowing rather than discovering. A clone has no live
database, so its tables come from parsing `schema.sql` — which means columns
added by idempotent `_ensure_*` ALTERs do not appear, and those are where most
recent columns live. That is acceptable *because* the agent can now open
connection.py and read the migrations itself (backend/research/code.py), which
is strictly better than a snapshot that happened to include them. For this
app's own scan, the live PRAGMA path is still used and still exact.

Nothing here reads ./data/ — that holds the user's DB and media.
"""
import ast
import json
import re
import subprocess
from pathlib import Path

# Extraction is capped so one pathological file can't produce a snapshot too
# large to fit in a prompt.
MAX_ROUTES = 600
MAX_COMPONENTS = 400
MAX_COMMITS = 40

# Stands in for a url_prefix that is a runtime parameter rather than a literal
# (the files.py blueprint factory, mounted twice at different prefixes).
_DYNAMIC_PREFIX = '{prefix}'

_SKIP_DIRS = {
    'node_modules', '.git', 'dist', 'data', '__pycache__', '.venv',
    'venv', '.pytest_cache', 'build', '.mypy_cache',
}


def repo_root() -> Path:
    """The checkout this app is running from.

    Resolved from the module's own location rather than the process CWD, which
    differs between `npm run dev`, the PyWebView launcher and pytest.
    """
    return Path(__file__).resolve().parents[2]


def is_repo(root: Path) -> bool:
    """A directory is only crawlable if it looks like this repo.

    Guards against a misconfigured root sending the extractors off across the
    filesystem.
    """
    return (root / 'backend' / 'db' / 'schema.sql').is_file()


def _run_git(root: Path, *args: str) -> str | None:
    """Git output, or None outside a repo / when git is missing."""
    try:
        out = subprocess.run(
            ['git', *args], cwd=root, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def git_facts(root: Path, since_sha: str | None = None) -> dict:
    """HEAD, branch, and the commits/diffstat since the previous snapshot."""
    sha = _run_git(root, 'rev-parse', 'HEAD')
    facts: dict = {
        'sha': sha,
        'branch': _run_git(root, 'rev-parse', '--abbrev-ref', 'HEAD'),
        'commits': [],
        'diffstat': '',
    }
    if not sha:
        return facts
    # A since_sha that is no longer reachable (rebased, or a snapshot from
    # another branch) makes the range invalid; fall back to the recent log.
    span = f'{since_sha}..HEAD' if since_sha and since_sha != sha else None
    log = _run_git(root, 'log', '--oneline', f'-{MAX_COMMITS}', *( [span] if span else [] ))
    if log is None and span:
        span = None
        log = _run_git(root, 'log', '--oneline', f'-{MAX_COMMITS}')
    facts['commits'] = [ln for ln in (log or '').splitlines() if ln]
    if span:
        facts['diffstat'] = _run_git(root, 'diff', '--stat', span) or ''
    return facts


def _decorator_routes(node: ast.FunctionDef, bp_names: dict[str, str]) -> list[dict]:
    """HTTP routes declared by @bp.get('/x') / @bp.route('/x', methods=[...])."""
    found = []
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
            continue
        obj = dec.func.value
        if not isinstance(obj, ast.Name) or obj.id not in bp_names:
            continue
        attr = dec.func.attr
        if not dec.args or not isinstance(dec.args[0], ast.Constant):
            continue
        rule = dec.args[0].value
        if not isinstance(rule, str):
            continue

        if attr in ('get', 'post', 'patch', 'put', 'delete'):
            methods = [attr.upper()]
        elif attr == 'route':
            methods = ['GET']
            for kw in dec.keywords:
                if kw.arg == 'methods' and isinstance(kw.value, (ast.List, ast.Tuple)):
                    methods = [
                        e.value for e in kw.value.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)
                    ]
        else:
            continue

        prefix = bp_names[obj.id]
        for method in methods:
            found.append({
                'method': method,
                'path': f'{prefix}{rule}' if rule != '/' else (prefix or '/'),
                'function': node.name,
                'doc': (ast.get_docstring(node) or '').split('\n')[0][:200] or None,
                'line': node.lineno,
            })
    return found


def route_facts(root: Path) -> list[dict]:
    """Every HTTP route, parsed rather than grepped.

    An `ast` walk (not a regex) because it resolves each decorator back to the
    Blueprint it was declared on, which is what supplies the url_prefix.
    """
    routes: list[dict] = []
    routes_dir = root / 'backend' / 'routes'
    if not routes_dir.is_dir():
        return routes

    for path in sorted(routes_dir.glob('*.py')):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (SyntaxError, OSError, UnicodeDecodeError):
            # One unparseable file must not lose the whole inventory.
            continue

        # Blueprint variable name -> url_prefix.
        bp_names: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            fn = node.value.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, 'attr', None)
            if name not in ('Blueprint', 'make_files_blueprint'):
                continue
            prefix = ''
            for kw in node.value.keywords:
                if kw.arg != 'url_prefix':
                    continue
                if isinstance(kw.value, ast.Constant):
                    prefix = kw.value.value or ''
                else:
                    # A blueprint factory (backend/routes/files.py) takes its
                    # prefix as a parameter, so it is only knowable at the call
                    # site. Say so rather than emitting a bare rule that looks
                    # like a real, mountable path.
                    prefix = _DYNAMIC_PREFIX
            if name == 'make_files_blueprint' and len(node.value.args) > 1:
                arg = node.value.args[1]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    prefix = arg.value
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bp_names[target.id] = prefix

        if not bp_names:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for route in _decorator_routes(node, bp_names):
                    route['file'] = str(path.relative_to(root))
                    routes.append(route)

    routes.sort(key=lambda r: (r['path'], r['method']))
    return routes[:MAX_ROUTES]


# CREATE TABLE <name> ( <body> ) — enough for a schema file, which is written
# by hand and formatted conventionally. Not a SQL parser, and does not need to
# be: the fallback path's job is to name tables and columns, and anything it
# cannot parse simply is not listed.
_CREATE_TABLE = re.compile(
    r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["\'`]?(\w+)["\'`]?\s*\((.*?)\n\)\s*;',
    re.IGNORECASE | re.DOTALL,
)
_CONSTRAINT_WORDS = (
    'primary', 'unique', 'check', 'foreign', 'constraint', 'references',
)


def table_facts_from_sql(root: Path) -> list[dict]:
    """Tables parsed out of a repo's schema file, for a checkout with no live DB.

    Strictly weaker than the PRAGMA path — see the module docstring — and used
    only for a *registered* repository, where there is no running database to
    ask. Returns [] for a repo with no recognisable schema file, which is most
    of them.
    """
    for rel in ('backend/db/schema.sql', 'db/schema.sql', 'schema.sql'):
        text = _read(root, rel)
        if text:
            break
    else:
        return []

    tables = []
    for match in _CREATE_TABLE.finditer(text):
        name, body = match.group(1), match.group(2)
        columns = []
        for definition in _split_columns(body):
            # Split on whitespace *or* '(' so a table-level `UNIQUE(a, b)` is
            # recognised as a constraint rather than read as a column named
            # "UNIQUE(a,".
            first = re.split(r'[(\s]', definition, maxsplit=1)[0].strip('"`\'').lower()
            if first in _CONSTRAINT_WORDS:
                continue
            parts = definition.split()
            columns.append({
                'name': parts[0].strip('"`\''),
                'type': parts[1] if len(parts) > 1 else '',
                'notnull': 'NOT NULL' in definition.upper(),
                'pk': 'PRIMARY KEY' in definition.upper(),
            })
        if columns:
            tables.append({'table': name, 'columns': columns, 'virtual': False})
    tables.sort(key=lambda t: t['table'])
    return tables


def _split_columns(body: str) -> list[str]:
    """A CREATE TABLE body split into definitions, on depth-0 commas.

    Not on newlines: a column whose CHECK constraint wraps to the next line
    would otherwise be read as a second column called `CHECK(clone_state`. And
    not on every comma either, since `CHECK(x IN ('a','b'))` is full of them.
    Comments are stripped first, because a `--` line can contain anything.
    """
    cleaned = '\n'.join(line.split('--', 1)[0] for line in body.splitlines())
    out, current, depth = [], [], 0
    for char in cleaned:
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        if char == ',' and depth == 0:
            out.append(''.join(current))
            current = []
            continue
        current.append(char)
    out.append(''.join(current))
    return [d.strip() for d in out if d.strip()]


def table_facts(db) -> list[dict]:
    """Tables and columns from the live DB.

    Deliberately the live connection rather than a parse of schema.sql: by
    construction it has schema.sql *plus* every idempotent `_ensure_*` migration
    applied, and the migrations are where most recent columns live. Only
    available for the checkout this app is running from; a registered clone
    falls back to `table_facts_from_sql`.
    """
    tables = []
    rows = db.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table'"
        " AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for row in rows:
        name = row['name']
        cols = [
            {'name': c[1], 'type': c[2], 'notnull': bool(c[3]), 'pk': bool(c[5])}
            for c in db.execute(f'PRAGMA table_info("{name}")').fetchall()
        ]
        tables.append({
            'table': name,
            'columns': cols,
            # FTS5 virtual tables and their shadow tables are noise in an
            # inventory; flag rather than drop, so "is there an FTS index?"
            # stays answerable.
            'virtual': 'VIRTUAL TABLE' in (row['sql'] or '').upper(),
        })
    return tables


def _read(root: Path, rel: str) -> str:
    try:
        return (root / rel).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return ''


def _string_list(text: str, pattern: str) -> list[str]:
    """The quoted strings inside the first array literal matching `pattern`."""
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return []
    return re.findall(r"'([a-zA-Z0-9_-]+)'", match.group(1))


def view_facts(root: Path) -> dict:
    """The frontend's views, plus a cross-check of the three hand-synced lists.

    `VIEWS`, `navItems` and `VIEW_ORDER` are maintained by hand in three files
    and drift silently — a view missing from `VIEW_ORDER` simply can't be
    reached by the keyboard. Reporting the disagreement costs one set
    comparison, so the nightly snapshot may as well catch it.
    """
    views = _string_list(
        _read(root, 'src/lib/viewPersistence.ts'),
        r'export const VIEWS\s*=\s*\[(.*?)\]\s*as const',
    )
    sidebar = _read(root, 'src/components/Sidebar.tsx')
    nav_match = re.search(r'export const navItems[^=]*=\s*\[(.*?)\n\];', sidebar, re.DOTALL)
    nav_items = []
    if nav_match:
        nav_items = [
            {'view': v, 'label': lbl}
            for v, lbl in re.findall(
                r"view:\s*'([^']+)',\s*label:\s*'([^']+)'", nav_match.group(1)
            )
        ]
    order = _string_list(
        _read(root, 'src/shortcuts/ShortcutProvider.tsx'),
        r'export const VIEW_ORDER:\s*AppView\[\]\s*=\s*\[(.*?)\];',
    )

    nav_views = [n['view'] for n in nav_items]
    warnings = []
    if views and nav_views and set(views) != set(nav_views):
        missing = sorted(set(views) ^ set(nav_views))
        warnings.append(f'VIEWS and Sidebar navItems disagree: {", ".join(missing)}')
    if nav_views and order and nav_views != order:
        warnings.append('Sidebar navItems and VIEW_ORDER are not in the same order')

    return {'views': views, 'navItems': nav_items, 'viewOrder': order, 'warnings': warnings}


def api_facts(root: Path) -> list[dict]:
    """The `api.<namespace>` surface in src/hooks/api.ts."""
    text = _read(root, 'src/hooks/api.ts')
    match = re.search(r'export const api = \{(.*)\n\};', text, re.DOTALL)
    if not match:
        return []
    body = match.group(1)
    namespaces = []
    # Namespaces are the two-space-indented keys; their methods are the
    # four-space-indented ones beneath.
    for ns_match in re.finditer(r'\n  (\w+): \{(.*?)\n  \},', body, re.DOTALL):
        methods = re.findall(r'\n    (\w+):', ns_match.group(2))
        namespaces.append({'namespace': ns_match.group(1), 'methods': methods})
    return namespaces


def component_facts(root: Path) -> list[dict]:
    """React components by path and size, excluding tests."""
    out = []
    base = root / 'src' / 'components'
    if not base.is_dir():
        return out
    for path in sorted(base.rglob('*.tsx')):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if '.test.' in path.name:
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        out.append({
            'file': str(path.relative_to(root)),
            'lines': text.count('\n') + 1,
            'exports': re.findall(r'export function (\w+)', text),
        })
    return out[:MAX_COMPONENTS]


def ai_facts(root: Path) -> list[dict]:
    """backend/ai modules and the first line of each module docstring."""
    out = []
    base = root / 'backend' / 'ai'
    if not base.is_dir():
        return out
    for path in sorted(base.glob('*.py')):
        if path.name == '__init__.py':
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        doc = (ast.get_docstring(tree) or '').strip().split('\n')[0]
        out.append({'module': path.stem, 'purpose': doc or None})
    return out


def settings_facts(db) -> list[str]:
    """Column names on the single-row settings table."""
    return [c[1] for c in db.execute('PRAGMA table_info(settings)').fetchall()]


# Human-authored docs. These are read and indexed, never regenerated — they are
# denser than anything the model would write, and ROADMAP/TODO are specifically
# the "planned but not built" ledger that makes "is this already on the list?"
# answerable.
_DOC_FILES = ('CLAUDE.md', 'docs/architecture.md', 'docs/ROADMAP.md', 'docs/TODO.md')


def doc_facts(root: Path) -> list[dict]:
    """Headings from the human-maintained docs, with roadmap items verbatim."""
    out = []
    for rel in _DOC_FILES:
        text = _read(root, rel)
        if not text:
            continue
        headings = [
            {'level': len(m.group(1)), 'text': m.group(2).strip()}
            for m in re.finditer(r'^(#{1,4}) +(.+)$', text, re.MULTILINE)
        ]
        entry: dict = {'path': rel, 'headings': headings, 'bytes': len(text)}
        if rel in ('docs/ROADMAP.md', 'docs/TODO.md'):
            # Bullet text verbatim: the assessor needs to compare an idea
            # against what is already written down, not against a paraphrase.
            entry['items'] = [
                m.group(1).strip()[:300]
                for m in re.finditer(r'^- \*\*(.+?)\*\*', text, re.MULTILINE)
            ]
        out.append(entry)
    return out


def build_facts(
    root: Path | None = None,
    db=None,
    since_sha: str | None = None,
    *,
    live_db: bool = True,
) -> dict:
    """The whole deterministic inventory: the generic scan plus whatever
    specific extractors this repo's shape supports.

    `live_db=False` is the registered-repository case: there is no running
    database for someone else's checkout, so tables come from its schema file
    and there are no settings columns to report. Each specific extractor
    already returns [] when its directory is absent, so a Rust repo simply
    yields the generic half without anything having to detect that.
    """
    from backend.research import repo_scan

    root = root or repo_root()
    if live_db and db is None:
        from backend.db.connection import get_db
        db = get_db()

    facts = repo_scan.build_scan(root)
    facts['git'] = git_facts(root, since_sha)
    facts.update({
        'routes': route_facts(root),
        'tables': table_facts(db) if live_db else table_facts_from_sql(root),
        'views': view_facts(root),
        'api': api_facts(root),
        'components': component_facts(root),
        'ai': ai_facts(root),
        'settings': settings_facts(db) if live_db else [],
        # The specific doc list (CLAUDE.md, ROADMAP, TODO) where it applies;
        # repo_scan's generic sweep already found whatever else is there.
        'docs': doc_facts(root) or facts.get('docs') or [],
        # False means "tables here were parsed, not read" — the renderer says
        # so, because a missing column is otherwise indistinguishable from a
        # column that does not exist.
        'liveDb': live_db,
    })
    return facts


def render_digest(facts: dict, name: str = '') -> str:
    """Markdown rendering of the facts — the artifact the agent reads.

    Pure: no DB, no filesystem, no model. Kept compact enough to sit inside a
    slot alongside an idea and a tool transcript.

    The generic sections (languages, layout, modules) come first because they
    are the only ones every repo has; the specific ones follow and are simply
    absent when this repo has no Flask routes or React views to report.
    """
    title = f'# {name} repo inventory' if name else '# Repo inventory'
    lines: list[str] = [title, '']

    git = facts.get('git') or {}
    if git.get('sha'):
        lines += [f"Commit `{git['sha'][:10]}` on `{git.get('branch') or 'unknown'}`.", '']

    languages = facts.get('languages') or []
    if languages:
        lines += ['## Languages', '', ', '.join(
            f"{e['language']} ({e['files']} files, {e['lines']:,} lines)"
            for e in languages[:8]
        ), '']

    layout = facts.get('layout') or []
    if layout:
        lines += ['## Layout', '', ', '.join(layout), '']

    modules = facts.get('modules') or []
    if modules:
        lines += ['## Modules, largest first', '']
        for module in modules[:30]:
            lines.append(
                f"- `{module['path'] or '(root)'}` — {module['files']} files,"
                f" {module['lines']:,} lines"
            )
        remaining = facts.get('moduleCount', len(modules)) - min(30, len(modules))
        if remaining > 0:
            lines.append(f'- … and {remaining} more')
        lines.append('')

    views = facts.get('views') or {}
    nav = views.get('navItems') or []
    if nav:
        lines += ['## Views', '', ', '.join(f"{n['label']} (`{n['view']}`)" for n in nav), '']
    for warning in views.get('warnings') or []:
        lines += [f'> Inconsistency: {warning}', '']

    routes = facts.get('routes') or []
    if routes:
        lines += ['## HTTP routes', '']
        by_file: dict[str, list[dict]] = {}
        for route in routes:
            by_file.setdefault(route.get('file', '?'), []).append(route)
        for file in sorted(by_file):
            lines.append(f'### {file}')
            for route in by_file[file]:
                doc = f" — {route['doc']}" if route.get('doc') else ''
                lines.append(f"- `{route['method']} {route['path']}`{doc}")
            lines.append('')

    tables = [t for t in (facts.get('tables') or []) if not t.get('virtual')]
    if tables:
        lines += ['## Tables', '']
        if facts.get('liveDb') is False:
            # Say so: a column added by a migration is missing here, and a
            # missing column is otherwise indistinguishable from one that does
            # not exist — which is the difference between "add this" and
            # "this is already there".
            lines += [
                '> Parsed from the schema file, not read from a live database,'
                ' so columns added by migrations are not listed. Read the'
                ' migration code to be sure.',
                '',
            ]
        for table in tables:
            cols = ', '.join(c['name'] for c in table['columns'])
            lines.append(f"- **{table['table']}**: {cols}")
        lines.append('')

    api = facts.get('api') or []
    if api:
        lines += ['## API client namespaces', '']
        for ns in api:
            lines.append(f"- `api.{ns['namespace']}`: {', '.join(ns['methods'])}")
        lines.append('')

    ai = facts.get('ai') or []
    if ai:
        lines += ['## AI modules', '']
        for mod in ai:
            lines.append(f"- `backend/ai/{mod['module']}.py`" + (f" — {mod['purpose']}" if mod['purpose'] else ''))
        lines.append('')

    components = facts.get('components') or []
    if components:
        lines += ['## Components', '', ', '.join(
            Path(c['file']).name for c in components
        ), '']

    settings = facts.get('settings') or []
    if settings:
        lines += ['## Settings columns', '', ', '.join(settings), '']

    for doc in facts.get('docs') or []:
        if doc.get('items'):
            lines += [f"## Already written down in {doc['path']}", '']
            lines += [f'- {item}' for item in doc['items']]
            lines.append('')

    return '\n'.join(lines).rstrip() + '\n'


def facts_json(facts: dict) -> str:
    return json.dumps(facts, separators=(',', ':'))
