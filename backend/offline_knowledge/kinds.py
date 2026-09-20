"""What sort of archive a ZIM is, and which queries should reach it.

Pure: no DB, no libzim, no filesystem. Everything here is derived from the two
things a ZIM will always hand over cheaply -- its own metadata dict and its
filename -- so the whole policy is table-testable.

Two outputs, and they answer different questions.

`kind` is what the archive *is*, and it exists so one source cannot swamp the
others. A 107 GB Stack Overflow and a 739 KB DevDocs archive are both "one
archive" to a filename-ordered loop, which is how the search that ships today
would spend every result slot on the first one it opened. Quotas are applied
per kind, so the question "how much of the answer may Q&A occupy" has somewhere
to live.

`match_terms` is what the archive is *about*, and it exists because the DevDocs
collection is ~600 files. Opening all of them per query to discover that 598 had
nothing is not a search strategy. A docs archive is consulted only when the
query mentions something it is plausibly named after, which costs one indexed
SELECT rather than 600 mmaps.

Kiwix's own metadata is preferred over the filename wherever it is present: a
file can be renamed, `_category:` cannot. The filename rules are the fallback
for hand-built or oddly-tagged archives, not the primary signal.
"""
from __future__ import annotations

import re

ENCYCLOPEDIA = 'encyclopedia'
QA = 'qa'
DOCS = 'docs'
OTHER = 'other'
KINDS = (ENCYCLOPEDIA, QA, DOCS, OTHER)

# `_category:` values openZIM publishes that are reference works rather than
# discussion. Wiktionary is here on purpose: a dictionary answers the same
# shape of question an encyclopedia does.
_ENCYCLOPEDIA_CATEGORIES = {
    'wikipedia', 'wikibooks', 'wikiversity', 'wikivoyage', 'wikisource',
    'wiktionary', 'vikidia', 'wikiquote', 'wikinews', 'wikispecies',
}
_QA_CATEGORIES = {'stack_exchange', 'stackexchange'}
_DOCS_CATEGORIES = {'devdocs', 'gutenberg_devdocs'}

# Matched against the first underscore-separated part of the filename, which is
# the site or project name in every Kiwix-published archive.
_QA_HOSTS = re.compile(
    r'^(stackoverflow\.com|.*\.stackexchange\.com|askubuntu\.com|superuser\.com'
    r'|serverfault\.com|mathoverflow\.net|stackapps\.com)$'
)
_ENCYCLOPEDIA_PREFIXES = (
    'wikipedia', 'wikibooks', 'wikiversity', 'wikivoyage', 'wikisource',
    'wiktionary', 'wikiquote', 'wikinews', 'vikidia',
)

# Parts of a Kiwix filename that name the packaging rather than the subject.
# `<project>_<lang>_<scope>_<flavour>_<date>` is the convention; only the
# language is positional (see `_terms`), the rest can be matched anywhere.
_DATE = re.compile(r'^\d{4}(-\d{2})?(-\d{2})?$')
_LANG = re.compile(r'^[a-z]{2,3}(-[a-z0-9]{2,8})?$')
_FLAVOURS = {'maxi', 'mini', 'nopic', 'novid', 'nodet', 'full', 'basic'}
_SCOPES = {'all', 'top', 'questions', 'selection', 'complete'}
_TLDS = {'com', 'net', 'org', 'io', 'dev', 'info', 'co', 'edu', 'gov'}

_SPLIT = re.compile(r'[^a-z0-9]+')


def parse_tags(raw: str) -> tuple[dict[str, str], set[str]]:
    """Split a ZIM `Tags` string into its `key:value` flags and its bare tags.

    Kiwix writes them in one semicolon-separated string, mixing both shapes:
    `wikipedia;_category:wikipedia;_pictures:yes;_ftindex:yes`. Callers want
    them apart -- `_category:` decides the kind, the bare `devdocs` tag is the
    only marker a DevDocs archive carries.
    """
    flags: dict[str, str] = {}
    bare: set[str] = set()
    for chunk in (raw or '').split(';'):
        chunk = chunk.strip()
        if not chunk:
            continue
        key, sep, value = chunk.partition(':')
        if sep:
            flags[key.strip().lstrip('_').lower()] = value.strip().lower()
        else:
            bare.add(chunk.lower())
    return flags, bare


def _terms(name: str) -> list[str]:
    """Subject tokens from a Kiwix archive name, packaging stripped.

    `devdocs_en_lit_2026-07` -> `['devdocs', 'lit']`. The language is dropped
    *positionally* -- only the first language-shaped part is removed -- because
    a three-letter library name is indistinguishable from a language code and
    `lit` is the whole reason that archive would ever be searched.
    """
    stem = (name or '').lower()
    if stem.endswith('.zim'):
        stem = stem[:-4]
    parts = [p for p in stem.split('_') if p]
    if not parts:
        return []

    out: list[str] = []
    for token in _SPLIT.split(parts[0]):
        if token and token not in _TLDS:
            out.append(token)

    dropped_lang = False
    for part in parts[1:]:
        if _DATE.match(part):
            continue
        if part in _FLAVOURS or part in _SCOPES:
            continue
        if not dropped_lang and _LANG.match(part):
            dropped_lang = True
            continue
        for token in _SPLIT.split(part):
            if token and token not in _TLDS:
                out.append(token)

    seen: set[str] = set()
    return [t for t in out if not (t in seen or seen.add(t))]


def classify(metadata: dict | None, filename: str) -> tuple[str, str]:
    """Return `(kind, match_terms)` for one archive.

    `metadata` is the ZIM's own dict (`Tags`, `Name`, `Creator`); pass whatever
    could be read, including nothing. `filename` is the fallback and the source
    of the match terms when `Name` is absent.
    """
    metadata = metadata or {}
    flags, bare = parse_tags(str(metadata.get('Tags') or ''))
    category = flags.get('category', '')
    creator = str(metadata.get('Creator') or '').strip().lower()
    name = str(metadata.get('Name') or '').strip()

    terms = _terms(name) or _terms(filename)
    # The project name is in `Name` but the *subject* is sometimes only in the
    # filename (a renamed or re-flavoured download), so merge rather than pick.
    if name:
        for token in _terms(filename):
            if token not in terms:
                terms.append(token)

    kind = OTHER
    if category in _ENCYCLOPEDIA_CATEGORIES:
        kind = ENCYCLOPEDIA
    elif category in _QA_CATEGORIES:
        kind = QA
    elif category in _DOCS_CATEGORIES or 'devdocs' in bare or creator == 'devdocs':
        kind = DOCS
    else:
        head = (name or filename).lower().split('_')[0]
        if _QA_HOSTS.match(head):
            kind = QA
        elif head.startswith('devdocs'):
            kind = DOCS
        elif any(head.startswith(prefix) for prefix in _ENCYCLOPEDIA_PREFIXES):
            kind = ENCYCLOPEDIA

    return kind, ' '.join(terms)


def query_tokens(query: str) -> set[str]:
    """The tokens of a user/model query, for intersecting with `match_terms`."""
    return {t for t in _SPLIT.split((query or '').lower()) if len(t) > 1}


def matches_query(match_terms: str, tokens: set[str]) -> bool:
    """Whether a narrow archive is worth opening for a query.

    Deliberately an intersection rather than a score: this gate only decides
    whether to *look*, and the ranking that follows is where precision belongs.
    An archive with no terms at all is never selected by it -- it has told us
    nothing to match on, so it falls to the always-searched kinds or to nothing.
    """
    if not match_terms or not tokens:
        return False
    return bool(set(match_terms.split()) & tokens)
