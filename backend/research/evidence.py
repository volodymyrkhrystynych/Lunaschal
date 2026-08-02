"""Candidate evidence for "is this idea already built?".

Pure and LLM-free: given an idea and a repo snapshot's facts, produce a short
list of things in the codebase the idea *might* already be satisfied by, each
with a real, checkable location.

This exists so the model never writes a file path. It picks evidence **by index
into this list**, which makes it structurally incapable of citing something
that does not exist — and that is the whole difference between an evidence-
backed verdict and a confident hallucination.
"""
import json
import re

MAX_CANDIDATES = 25
MIN_WORD_LENGTH = 3

# Words that match everything in a personal life-management app and so carry no
# signal about which feature an idea is about.
_STOPWORDS = frozenset("""
a an and are as at be been but by can could do does for from get give had has
have how i if in into is it its just like make may me more most much my need
new not of on one only or other our out over own same should so some such than
that the their them then there these they this those to too use used using very
want was way we were what when where which while who why will with would you
your able add adding also always app application feature idea maybe really thing
things something anything support supports let lets able better good page view
screen ui tab data user users
""".split())


def _stem(word: str) -> str:
    """Crude singularisation. Enough to match "sketches" against "sketch"."""
    for suffix in ('ies', 'es', 's'):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return 'y' if suffix == 'ies' else word[: -len(suffix)]
    return word


def keywords(text: str) -> set[str]:
    """Content words from an idea, stemmed and stopworded."""
    words = re.findall(r'[a-z][a-z0-9_]*', (text or '').lower())
    out = set()
    for word in words:
        if len(word) < MIN_WORD_LENGTH or word in _STOPWORDS:
            continue
        out.add(word)
        out.add(_stem(word))
    return out


def _identifier_words(name: str) -> set[str]:
    """Split a route path, table name or filename into comparable words."""
    parts = re.split(r'[^a-zA-Z0-9]+', name or '')
    out: set[str] = set()
    for part in parts:
        if not part:
            continue
        # camelCase / PascalCase → words
        for chunk in re.findall(r'[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+', part):
            lowered = chunk.lower()
            if len(lowered) >= MIN_WORD_LENGTH:
                out.add(lowered)
                out.add(_stem(lowered))
    return out


def _score(words: set[str], target: str, weight: float) -> float:
    hits = words & _identifier_words(target)
    return weight * len(hits) if hits else 0.0


def gather_candidates(idea: dict, facts: dict, limit: int = MAX_CANDIDATES) -> list[dict]:
    """Ranked evidence candidates, each with a real location.

    Weights favour the things that most decisively answer "does this exist?":
    a table or a route is near-proof, a component filename is suggestive, a
    roadmap bullet says only that it was *planned*.
    """
    words = keywords(f"{idea.get('title', '')} {idea.get('content') or idea.get('rawContent') or ''}")
    if not words:
        return []

    scored: list[tuple[float, dict]] = []

    for table in facts.get('tables') or []:
        if table.get('virtual'):
            continue
        name = table['table']
        score = _score(words, name, 3.0)
        # A column name is weaker evidence than the table itself.
        for column in table.get('columns') or []:
            score += _score(words, column['name'], 0.5)
        if score:
            columns = ', '.join(c['name'] for c in (table.get('columns') or [])[:12])
            scored.append((score, {
                'kind': 'table', 'ref': name, 'file': 'backend/db/schema.sql',
                'line': None, 'detail': columns,
            }))

    for route in facts.get('routes') or []:
        score = _score(words, route['path'], 2.5) + _score(words, route.get('function') or '', 1.0)
        if score:
            scored.append((score, {
                'kind': 'route', 'ref': f"{route['method']} {route['path']}",
                'file': route.get('file'), 'line': route.get('line'),
                'detail': route.get('doc'),
            }))

    for component in facts.get('components') or []:
        score = _score(words, component['file'], 1.5)
        if score:
            scored.append((score, {
                'kind': 'component', 'ref': component['file'].split('/')[-1],
                'file': component['file'], 'line': None, 'detail': None,
            }))

    for namespace in facts.get('api') or []:
        score = _score(words, namespace['namespace'], 2.0)
        for method in namespace.get('methods') or []:
            score += _score(words, method, 0.4)
        if score:
            scored.append((score, {
                'kind': 'api', 'ref': f"api.{namespace['namespace']}",
                'file': 'src/hooks/api.ts', 'line': None,
                'detail': ', '.join((namespace.get('methods') or [])[:12]),
            }))

    for module in facts.get('ai') or []:
        score = _score(words, module['module'], 1.5)
        if score:
            scored.append((score, {
                'kind': 'ai', 'ref': f"backend/ai/{module['module']}.py",
                'file': f"backend/ai/{module['module']}.py", 'line': None,
                'detail': module.get('purpose'),
            }))

    for column in facts.get('settings') or []:
        score = _score(words, column, 1.0)
        if score:
            scored.append((score, {
                'kind': 'setting', 'ref': f'settings.{column}',
                'file': 'backend/db/connection.py', 'line': None, 'detail': None,
            }))

    for view in (facts.get('views') or {}).get('navItems') or []:
        score = _score(words, f"{view['view']} {view['label']}", 2.0)
        if score:
            scored.append((score, {
                'kind': 'view', 'ref': f"{view['label']} tab",
                'file': 'src/components/Sidebar.tsx', 'line': None, 'detail': None,
            }))

    # Ranked by score, then by kind so ties are stable rather than dict-ordered.
    scored.sort(key=lambda pair: (-pair[0], pair[1]['kind'], pair[1]['ref']))
    return [item for _, item in scored[:limit]]


def roadmap_matches(idea: dict, facts: dict, limit: int = 3) -> list[str]:
    """Roadmap/TODO bullets this idea resembles.

    Separate from the code evidence on purpose: "already on the roadmap" means
    *planned*, which is the opposite of *built*, and conflating the two is how
    a backlog item gets marked done because someone wrote it down.
    """
    words = keywords(f"{idea.get('title', '')} {idea.get('content') or idea.get('rawContent') or ''}")
    if not words:
        return []
    scored: list[tuple[int, str]] = []
    for doc in facts.get('docs') or []:
        for item in doc.get('items') or []:
            hits = len(words & _identifier_words(item))
            if hits:
                scored.append((hits, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [item for _, item in scored[:limit]]


def render_candidates(candidates: list[dict]) -> str:
    """The numbered list the model selects from. Indexes are 1-based in the
    prompt because models are markedly better at 1-based references."""
    lines = []
    for i, c in enumerate(candidates, start=1):
        location = c['file'] + (f":{c['line']}" if c.get('line') else '')
        detail = f" — {c['detail']}" if c.get('detail') else ''
        lines.append(f"{i}. [{c['kind']}] {c['ref']} ({location}){detail}")
    return '\n'.join(lines)


def select_by_index(candidates: list[dict], indexes) -> list[dict]:
    """Resolve the model's 1-based picks, dropping anything out of range.

    Out-of-range is dropped rather than raising: a stray index is the model
    miscounting, not a reason to lose an otherwise good assessment.
    """
    chosen: list[dict] = []
    seen: set[int] = set()
    for raw in indexes or []:
        try:
            index = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= index <= len(candidates) and index not in seen:
            seen.add(index)
            chosen.append(candidates[index - 1])
    return chosen


def evidence_json(evidence: list[dict]) -> str:
    return json.dumps(evidence)
