"""Reading a repository, as tools the model can call.

The Ideas agent shipped without any of this. Its whole picture of the codebase
was a pre-digested inventory (repo_facts.py: route strings, table names,
component filenames), and discuss.py's system prompt told it in so many words
not to go looking for anything else. That is a fine way to answer "does a
`paper_pages` table exist?" and no way at all to answer "is this loop racy?" or
"where would this actually go?" — the questions the tab exists for.

So: ripgrep, read, list, and a graph lookup. The same four moves a person makes.

Contract copied from web.py deliberately — a `TOOLS` list and a
`run_tool(name, args) -> (text_for_model, event_for_ui)` that **never raises**.
A failed tool is information the model can act on; an exception here would
abandon a run that is otherwise fine.

The one shape difference is that these tools are bound to a repository, and
agent._loop dispatches by calling `dispatch[name].run_tool(name, args)` — so
CodeTools is a small object satisfying that duck type. The shared loop is not
modified, which is the whole reason it takes `tools=`/`dispatch=` at all.
"""
import logging
import subprocess
from pathlib import Path

from backend.repos import graph, storage
from backend.research.repo_facts import _SKIP_DIRS

logger = logging.getLogger(__name__)

# A code pass reads more than a web pass fetches, and a read is cheap — no
# network, no rate limit. This is the ceiling that stops a model that keeps
# finding one more file, not a target.
MAX_READS = 40

MAX_FILE_CHARS = 12000
MAX_READ_LINES = 400
MAX_SEARCH_MATCHES = 60
MAX_DIR_ENTRIES = 200
SEARCH_TIMEOUT = 15

# Never source: build output, dependencies, VCS internals, and the graph we put
# there ourselves. Imported from repo_facts rather than copied, so the two
# cannot drift.
SKIP_DIRS = _SKIP_DIRS | {'graphify-out', '.next', '.turbo', 'target', 'vendor'}

# Reading one of these wastes a turn and tells the model nothing.
_BINARY_EXTS = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.pdf', '.zip', '.gz',
    '.tar', '.whl', '.so', '.dylib', '.dll', '.exe', '.woff', '.woff2', '.ttf',
    '.mp3', '.mp4', '.wav', '.epub', '.db', '.sqlite', '.bin', '.pyc',
}


def tools_for(root: Path | None) -> list[dict]:
    """The toolbox for one repo.

    `code_map` is present only when that repo actually has a graph and graphify
    is installed. Offering a tool that always answers "unavailable" spends a
    turn to learn nothing — the same degrade-by-absence `web.is_search_configured`
    already uses, and the reason a repo with no graph is fully usable.
    """
    tools = list(_BASE_TOOLS)
    if graph.has_graph(root) and graph.graphify_bin():
        tools.append(_CODE_MAP_TOOL)
    return tools


def tool_names(root: Path | None) -> list[str]:
    return [t['function']['name'] for t in tools_for(root)]


def dispatch_for(tools: 'CodeTools', root: Path | None) -> dict:
    """The name→handler map agent._loop wants, for exactly the tools offered.

    Built from `tool_names` rather than a literal, because a tool the model can
    see but the dispatch cannot run comes back as "Unknown tool" — which reads
    to the model as a broken tool rather than as one it should not have called.
    """
    return {name: tools for name in tool_names(root)}


class CodeTools:
    """Repo-scoped code tools.

    One instance per run: the read budget and the read log are per-run state,
    and sharing an instance would let an earlier pass exhaust a later one.
    """

    def __init__(self, root: Path, max_reads: int = MAX_READS):
        self.root = Path(root)
        self.max_reads = max_reads
        self.reads = 0
        # Every file this run actually opened, in order, deduplicated. This is
        # the provenance for a wiki article and the candidate list for a plan's
        # filesToTouch: what was read, never what the model says it read.
        self.files_read: list[str] = []

    # --- helpers ---

    def _resolve(self, rel: str) -> Path | None:
        return storage.resolve_within(self.root, rel)

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root.resolve()))
        except ValueError:
            return str(path)

    def _strip_root(self, line: str) -> str:
        """rg was given an absolute path, so its output carries one. The model
        must only ever see repo-relative paths — they are what a plan cites and
        what a coding agent will go looking for."""
        return line.replace(f'{self.root.resolve()}/', '')

    # --- tools ---

    def code_search(self, query: str, path: str = '', glob: str = '') -> tuple[str, dict]:
        query = (query or '').strip()
        if not query:
            return ('code_search needs a query.',
                    {'tool': 'code_search', 'ok': False, 'error': 'empty query'})

        # Always hand rg an absolute path, so `_strip_root` has a fixed prefix
        # to remove no matter what the root was constructed from. A relative
        # root would make rg echo './backend/...' and leak the dot into a
        # citation.
        target = self.root.resolve()
        if path:
            resolved = self._resolve(path)
            if resolved is None or not resolved.exists():
                return (
                    f'No such path in this repo: {path}',
                    {'tool': 'code_search', 'arg': query, 'ok': False, 'error': 'bad path'},
                )
            target = resolved

        args = [
            'rg', '--line-number', '--no-heading', '--color', 'never',
            '--max-count', '3', '--max-columns', '200',
            '--max-filesize', '2M', '-i',
        ]
        for skip in sorted(SKIP_DIRS):
            args += ['--glob', f'!{skip}/']
        # Defence in depth: resolve_within already refuses these by path, but rg
        # walks the tree itself and would otherwise print a matching line out of
        # one before anybody asked to read it.
        for secret in ('.env*', '*.pem', '*.key', 'id_rsa*'):
            args += ['--glob', f'!{secret}']
        if glob:
            args += ['--glob', glob]
        args += ['--regexp', query, '--', str(target)]

        try:
            out = subprocess.run(args, capture_output=True, text=True,
                                 timeout=SEARCH_TIMEOUT, cwd=str(self.root))
        except subprocess.TimeoutExpired:
            return (
                f'Search for "{query}" took too long. Try a narrower pattern, '
                'or pass a path to search inside.',
                {'tool': 'code_search', 'arg': query, 'ok': False, 'error': 'timeout'},
            )
        except OSError as e:
            return (f'Could not search: {e}',
                    {'tool': 'code_search', 'arg': query, 'ok': False, 'error': str(e)})

        # rg exits 1 for "no matches", which is a result and not a failure.
        if out.returncode not in (0, 1):
            message = (out.stderr or '').strip()[:200]
            return (f'Search failed: {message}',
                    {'tool': 'code_search', 'arg': query, 'ok': False, 'error': message})

        lines = [ln for ln in (out.stdout or '').splitlines() if ln.strip()]
        if not lines:
            return (f'No matches for "{query}".',
                    {'tool': 'code_search', 'arg': query, 'ok': True, 'count': 0})

        clipped = lines[:MAX_SEARCH_MATCHES]
        text = '\n'.join(self._strip_root(ln) for ln in clipped)
        if len(lines) > len(clipped):
            text += (
                f'\n\n… {len(lines) - len(clipped)} more matches not shown. '
                'Narrow the pattern, or pass a path, if you need the rest.'
            )
        return (text,
                {'tool': 'code_search', 'arg': query, 'ok': True, 'count': len(clipped)})

    def read_file(self, path: str, start=None, end=None) -> tuple[str, dict]:
        rel = (path or '').strip()
        resolved = self._resolve(rel)
        if resolved is None:
            return (
                f'Refusing to read {rel or "(nothing)"}: it is outside the repo, '
                'or it is a credential file.',
                {'tool': 'read_file', 'arg': rel, 'ok': False, 'error': 'refused'},
            )
        if not resolved.is_file():
            return (f'No such file: {rel}',
                    {'tool': 'read_file', 'arg': rel, 'ok': False, 'error': 'missing'})
        if resolved.suffix.lower() in _BINARY_EXTS:
            return (f'{rel} is a binary file; there is nothing to read.',
                    {'tool': 'read_file', 'arg': rel, 'ok': False, 'error': 'binary'})

        if self.reads >= self.max_reads:
            return (
                'Read budget for this run is exhausted. Work with what you have.',
                {'tool': 'read_file', 'arg': rel, 'ok': False,
                 'error': 'read budget exhausted'},
            )

        try:
            text = resolved.read_text(encoding='utf-8', errors='replace')
        except OSError as e:
            return (f'Could not read {rel}: {e}',
                    {'tool': 'read_file', 'arg': rel, 'ok': False, 'error': str(e)})

        self.reads += 1
        rel_path = self._rel(resolved)
        if rel_path not in self.files_read:
            self.files_read.append(rel_path)

        lines = text.splitlines()
        total = len(lines)
        first = max(1, min(_as_int(start, 1), total or 1))
        last = _as_int(end, first + MAX_READ_LINES - 1)
        last = max(first, min(last, total, first + MAX_READ_LINES - 1))

        window = lines[first - 1:last]
        body = '\n'.join(f'{first + i}\t{ln}' for i, ln in enumerate(window))
        if len(body) > MAX_FILE_CHARS:
            body = body[:MAX_FILE_CHARS] + '\n… truncated.'

        more = ''
        if last < total:
            more = (
                f'\n\n… {total - last} more lines. Call read_file again with '
                f'start={last + 1} if you need them.'
            )
        return (
            f'{rel_path} (lines {first}-{last} of {total})\n\n{body}{more}',
            {'tool': 'read_file', 'arg': rel_path, 'ok': True,
             'file': rel_path, 'line': first, 'lines': total},
        )

    def list_dir(self, path: str = '') -> tuple[str, dict]:
        rel = (path or '').strip().strip('/')
        resolved = self._resolve(rel) if rel else self.root.resolve()
        if resolved is None or not resolved.is_dir():
            return (f'No such directory: {rel or "/"}',
                    {'tool': 'list_dir', 'arg': rel, 'ok': False, 'error': 'missing'})

        entries = []
        try:
            for child in sorted(resolved.iterdir(), key=lambda p: (p.is_file(), p.name)):
                if child.name in SKIP_DIRS or child.name.startswith('.env'):
                    continue
                if child.is_dir():
                    entries.append(f'{child.name}/')
                else:
                    try:
                        size = child.stat().st_size
                    except OSError:
                        size = 0
                    entries.append(f'{child.name}  ({size:,} bytes)')
        except OSError as e:
            return (f'Could not list {rel or "/"}: {e}',
                    {'tool': 'list_dir', 'arg': rel, 'ok': False, 'error': str(e)})

        if not entries:
            return (f'{rel or "/"} is empty.',
                    {'tool': 'list_dir', 'arg': rel, 'ok': True, 'count': 0})

        clipped = entries[:MAX_DIR_ENTRIES]
        text = f'{rel or "/"}:\n' + '\n'.join(f'- {e}' for e in clipped)
        if len(entries) > len(clipped):
            text += f'\n… {len(entries) - len(clipped)} more entries.'
        return (text,
                {'tool': 'list_dir', 'arg': rel or '/', 'ok': True, 'count': len(clipped)})

    def code_map(self, query: str) -> tuple[str, dict]:
        query = (query or '').strip()
        if not query:
            return ('code_map needs a query.',
                    {'tool': 'code_map', 'ok': False, 'error': 'empty query'})
        answer = graph.query(self.root, query)
        if not answer:
            return (
                f'The code map has nothing for "{query}". Try code_search instead.',
                {'tool': 'code_map', 'arg': query, 'ok': True, 'count': 0},
            )
        return (answer + _CODE_MAP_REMINDER,
                {'tool': 'code_map', 'arg': query, 'ok': True})

    # --- dispatch ---

    def run_tool(self, name: str, args: dict) -> tuple[str, dict]:
        """Never raises, for the same reason web.run_tool doesn't."""
        try:
            if name == 'code_search':
                return self.code_search(args.get('query') or '',
                                        args.get('path') or '', args.get('glob') or '')
            if name == 'read_file':
                return self.read_file(args.get('path') or '',
                                      args.get('start'), args.get('end'))
            if name == 'list_dir':
                return self.list_dir(args.get('path') or '')
            if name == 'code_map':
                return self.code_map(args.get('query') or '')
        except Exception as e:  # pragma: no cover — the guarantee, not a path
            logger.warning('Code tool %s failed: %s', name, e)
            return (f'{name} failed: {e}', {'tool': name, 'ok': False, 'error': str(e)})
        return (f'Unknown tool: {name}', {'tool': name, 'ok': False, 'error': 'unknown tool'})


def _as_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def files_read(steps: list[dict]) -> list[dict]:
    """The files a run actually opened, taken from its own steps.

    Provenance from what happened rather than from what the model claims — the
    same rule agent._loop applies to web_fetch, applied to reads. This is what
    a wiki article records as its sources and what bounds a plan's file list.
    """
    seen: dict[str, dict] = {}
    for step in steps or []:
        if step.get('tool') == 'read_file' and step.get('ok') and step.get('file'):
            seen.setdefault(step['file'], {'file': step['file'], 'line': step.get('line')})
    return list(seen.values())


# The same lesson as web.READ_ONE_REMINDER, which was measured rather than
# guessed (docs/ideas-tab.md#why-it-never-read-a-page): an instruction thousands
# of tokens back in the system prompt loses to one riding on the freshest
# message. The map lists symbols and their files; the failure it prevents is
# answering from the index without ever opening what it points at.
_CODE_MAP_REMINDER = (
    '\n\nThese are locations, not code. Open the most relevant of these files '
    'with read_file before drawing any conclusion from them.'
)

_BASE_TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'code_search',
            'description': (
                'Search the repository for a regular expression, like ripgrep. '
                'Returns path:line: matching-text. Use this to find where '
                'something is before reading it.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string',
                              'description': 'A regular expression, case-insensitive.'},
                    'path': {'type': 'string',
                             'description': 'Optional subdirectory or file to search in.'},
                    'glob': {'type': 'string',
                             'description': "Optional filename filter, e.g. '*.py'."},
                },
                'required': ['query'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'read_file',
            'description': (
                'Read a file from the repository, with line numbers. Give start '
                'and end to read one region of a long file.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string',
                             'description': 'Repo-relative path, e.g. backend/app.py.'},
                    'start': {'type': 'integer', 'description': 'First line (1-based).'},
                    'end': {'type': 'integer', 'description': 'Last line.'},
                },
                'required': ['path'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'list_dir',
            'description': (
                'List a directory in the repository. Call with no path for the '
                'repository root.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string', 'description': 'Repo-relative directory.'},
                },
            },
        },
    },
]

_CODE_MAP_TOOL = {
    'type': 'function',
    'function': {
        'name': 'code_map',
        'description': (
            "Look a concept up in this repository's code graph and get back the "
            'symbols and files involved, with line numbers. Much cheaper than '
            'searching when you do not yet know what things are called.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {'query': {'type': 'string'}},
            'required': ['query'],
        },
    },
}
