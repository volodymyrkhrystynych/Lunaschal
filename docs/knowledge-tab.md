# Knowledge tab

**Status:** implemented as a federated offline reader and chat source over
any number of Kiwix ZIM archives.

This document records the current scope, the decisions behind it, and the work
that is deliberately deferred. Chat context retention and compaction are
covered separately in
[`chat-knowledge-and-compaction.md`](./chat-knowledge-and-compaction.md).

## Current product scope

The library is one or more current ZIM archives on the user's archive drive.
The user downloads and updates them outside Lunaschal, points Settings at the
containing folder, and Lunaschal reads them in place.

Three source families are supported and searched together:

- **encyclopedias** — Wikipedia and its siblings;
- **Q&A** — the Stack Exchange network, including the 107 GB
  `stackoverflow.com_en_all`; and
- **docs** — the DevDocs collection, several hundred per-technology archives.

Anything else is `other` and is still searched. Still out of scope:

- no historical pre/post-2022 split;
- no in-app Kiwix catalogue, downloads, or updates (see the deferred roadmap —
  this is the next branch); and
- no separate Kiwix HTTP server.

### Why the library had to become plural before the sources were worth adding

The first version walked archives in filename order and stopped as soon as the
global result limit was full. With one Wikipedia installed that is invisible.
With two archives of very different sizes it is fatal:
`stackoverflow.com_en_all` sorts before `wikipedia_`, so it would have spent
every result slot before an encyclopedia was opened. Adding Stack Exchange to
the old search would have quietly replaced the library with Stack Overflow.

Two further facts, both measured rather than assumed, shaped the fix:

- **Parallelism does not pay.** Sixteen warm searches across four distinct
  `Archive` objects took 0.097 s serially and 0.109 s through a four-thread
  pool — python-libzim holds the GIL for the duration of a search. The lever is
  searching _fewer_ archives, not searching them at once.
- **Half the interesting archives have no fulltext index.** Every DevDocs ZIM
  Kiwix publishes is tagged `_ftindex:no`, as are 7 of the 181 Stack Exchange
  ones. `Searcher` returns nothing at all for those. They answer through
  `libzim.suggestion.SuggestionSearcher`, which queries the title index — so a
  no-fulltext archive is a normal archive here, not a broken one, and the UI
  says "title search only" in amber rather than flagging an error.

## Why libzim is embedded

Lunaschal uses the Python `libzim` bindings directly from Flask. A separate
`kiwix-serve` process would add another service to install, start, supervise,
secure, and connect on every machine without currently providing a needed
boundary. The archive remains read-only either way.

The embedded arrangement gives the app:

- direct archive discovery and metadata;
- local full-text search using the index shipped inside the ZIM;
- article and static-resource reads without copying or extracting the archive;
- the same API in desktop and browser/LAN modes; and
- direct main-agent tools without a second network dependency.

`kiwix-serve` remains an option if future profiling shows that a very large,
mixed collection needs Kiwix's library management or process isolation. It is
not required for the Wikipedia-first version.

## How a federated search works

`backend/offline_knowledge/archive.py`'s `search_many` runs three stages.

1. **Select**, from the registry only — no archive is opened in order to decide
   whether to open it. Each _kind_ contributes at most `MAX_ARCHIVES_PER_CLASS`
   (8). A class smaller than that is searched in full; a large one (DevDocs at
   several hundred, Stack Exchange at 181) is narrowed to the archives the
   query actually names, through the `match_terms` tokens derived from the ZIM
   name — `devdocs_en_lit_2026-07` yields `devdocs lit`. When fewer than 8
   match, the rest is topped up by article count, so a programming question
   with no term match still reaches Stack Overflow rather than reaching
   nothing.
2. **Search, archive-outer and query-inner.** Each archive is opened once and
   asked all of the (up to four) query variants, so a model search costs N
   archive visits rather than 4N. The fetch depth is a flat per-archive number
   and deliberately _not_ the output quota: fetching only as many as may be
   returned leaves nothing to redistribute when another class under-delivers,
   which showed up as a two-archive library answering 7 of a requested 10 while
   a 50-hit archive sat right there.
3. **Merge globally.** Deduplicate on `(archiveId, path)`, then rank by title
   match first. That is the cross-archive equaliser: rank position inside a
   739 KB DevDocs index and inside a 30-million-article Stack Overflow index
   mean nothing to each other, whereas `_title_match` is comparable everywhere.
   Class quotas (`encyclopedia` .40, `qa` .30, `docs` .20, `other` .10,
   normalized over the classes actually present) then bound the output, and a
   final pass tops the list up from whatever is left — which is what
   redistributes the share of a class the library does not have.

A search reports `searched`, `skipped` and `tookMs` alongside its results, and
the reader surfaces a line whenever anything was skipped. A search that quietly
consulted 3 of 600 archives is the failure this whole design exists to prevent,
so it must not be silent.

## The archive registry

`knowledge_archives` (one row per file, keyed on the existing `archive_id`
path hash) caches what a scan found, plus the two things a scan cannot know:
whether the user wants an archive searched, and whether it is healthy. It
exists because both hot paths used to touch the filesystem — search rglobbed
the root, and `_resolve()` rglobbed it again on _every article read_, which
with hundreds of archives is a directory walk per image in a rendered page.
Both are now indexed SELECTs.

Three properties worth knowing:

- **`kind` is derived from ZIM metadata, not the filename, where it can be.**
  `Tags`'s `_category:` is authoritative (`wikipedia`, `stack_exchange`); the
  bare `devdocs` tag and `Creator=DevDocs` identify docs; filename patterns are
  the fallback for hand-built archives. A user correction sets
  `kind_source='user'` and survives every later rescan.
- **A renamed file re-adopts its row** through `zim_uuid`, so it does not come
  back as a fresh, default-on archive with its disabled flag forgotten.
- **An unavailable root is not an emptied library.** `sync()` returns early
  when the root is unconfigured or not a directory, rather than marking every
  row `missing` because an external drive was not mounted yet. A file that is
  genuinely gone while the root _is_ mounted is marked `missing` and kept, not
  deleted.

## Requirements and configuration

Python dependencies live in `requirements.txt`:

- `libzim>=3.7` for archive metadata, search, and entry reads;
- `beautifulsoup4>=4.12` for rebasing archived HTML links.

Settings → **Knowledge Library** writes the selected directory to
`settings.knowledge_root`. `KNOWLEDGE_ROOT` is the fallback when that database
value is empty. ZIM files are discovered recursively and never modified.

The demo launcher rebuilds `data/test-run/lunaschal-test.db` every time it
starts. A folder selected through the test UI is therefore temporary and must
be selected again after a rebuild. Production settings persist normally.

## Reader flow

The top-level Knowledge view is implemented in
`src/components/Knowledge/Knowledge.tsx`:

1. Read the configured root.
2. List archives grouped by kind when no query is active, each with an
   enable toggle and a health badge, plus a Rescan button.
3. Search every relevant archive when the user submits a query.
4. Show matching titles with the kind and archive each came from, and a
   coverage line when anything was skipped.
5. Load the selected archived page in a sandboxed iframe.

The Flask API is in `backend/routes/knowledge.py`:

| Endpoint                                          | Purpose                                                      |
| ------------------------------------------------- | ------------------------------------------------------------ |
| `GET /api/knowledge/config`                       | Return the configured directory and whether it exists.       |
| `PUT /api/knowledge/config`                       | Validate and save a directory, then force a rescan.          |
| `GET /api/knowledge/archives`                     | Every known archive, disabled and unhealthy ones included.   |
| `POST /api/knowledge/archives/rescan`             | Reopen and reclassify everything under the root.             |
| `PATCH /api/knowledge/archives/<id>`              | Set `enabled`, or correct `kind` (sticky against rescans).   |
| `POST /api/knowledge/archives/<id>/verify`        | libzim's own integrity check, in a background thread.        |
| `GET /api/knowledge/search?q=…&kind=…`            | Federated search; returns results plus `searched`/`skipped`. |
| `GET /api/knowledge/archives/<id>/content/<path>` | Read an article or static resource.                          |

`verify` is deliberately never part of a scan: `Archive.check()` hashes the
entire file, which on a 107 GB archive is minutes of disk. It answers "this
archive behaves oddly", and nothing waits on it.

Archived HTML is treated as an untrusted document. Root-relative links are
rebased through the content endpoint, scripts and forms are disabled by CSP,
and the iframe has an empty sandbox permission set. Lunaschal never writes into
the archive directory.

## Chat retrieval flow

Offline retrieval belongs to the main chat agent because it must judge whether
the evidence actually answers the user's question. Raw web research remains
behind the delegate because page dumps are large and should cross into the
conversation only as a cited summary.

For factual and reference questions, the main agent follows this flow:

```text
user question
    |
    v
create 2–4 local query variants in one tool call
    |
    v
search each variant -> merge -> de-duplicate -> title-rank
    |
    v
main agent chooses and reads 1–3 plausible articles
    |
    +-- sufficient/current enough --> answer from local evidence
    |
    +-- insufficient/stale ---------> web research delegate
```

The variants normally include:

- the clean entity or title;
- the user's complete question; and
- plausible interpretations, such as book versus movie.

They must not include an answer the model merely guessed. The backend searches
up to four unique variants, takes a bounded candidate set from each, de-duplicates
by archive and entry path, and boosts exact or parenthetical title matches. The
main model is the semantic selector for the merged list; a separate neural
reranker is not part of this version.

Titles and search results are leads, not evidence. The agent must read an
article before relying on or citing it. When multiple meanings remain plausible,
it should read them and answer each explicitly or ask for clarification.

The delegate may be used immediately for inherently current information. For
other factual questions, code-level gates require a local search first and, when
it found candidates, at least one local read before web delegation.

## Evidence and conversation context

A successful local read contributes:

- the full capped article text to the current answer turn;
- a clickable source URL;
- stable archive ID and entry path;
- archive title and snapshot date; and
- a bounded excerpt persisted in assistant-message metadata.

Recent excerpts can return as context on a follow-up without rerunning the
search. The stable identity lets a later turn reopen the article. Raw tool
results are not replayed forever, and compaction never deletes or rewrites the
original chat messages.

## Known limitations

These are known gaps, not promises already provided by the UI:

1. **Read sources are not yet distinguished from cited sources.** An article
   the agent inspected and rejected can still appear below the answer as if it
   supported the claim.
2. **Chat links do not deep-link into the Knowledge view.** They open the raw
   content endpoint instead of selecting the article in the reader.
3. **Reader navigation is minimal.** There is no app-level back/forward history,
   persistent selected article, bookmarks, annotations, or article table of
   contents.
4. **Manual search has no pagination or language filter.** It can be scoped to
   a `kind`, but not paged, and has no LLM-generated query expansion.
5. **Search snippets may be empty.** The installed `libzim` binding yields
   entry paths rather than rich result objects, and a title-index hit has no
   snippet to give by construction.
6. **External hyperlink navigation is not explicitly intercepted.** Remote
   subresources and scripts are blocked, but the offline-only promise needs a
   browser test and an explicit policy for clicked external links.
7. **`match_terms` is a token intersection, not an understanding.** A DevDocs
   archive is reached when the query names it (`rust`, `lit`); a question
   phrased purely in symbols (`useEffect cleanup`) matches no archive name and
   falls back to the top-8-by-article-count. This is the weakest part of the
   design and the most likely thing to revisit.
8. **Cold-search cost on a 107 GB archive is unmeasured.** All timings above
   are warm; the time budget exists partly because of that uncertainty.

## Deferred roadmap

### Done — kept here as the record of what unblocked the new sources

Fair per-archive quotas with a global merge, per-archive enable/disable,
source/kind metadata, health states, and the title-index path all shipped on
`feat/knowledge-federated-search`. What is still open from that list: how
duplicate articles and multiple snapshot dates should rank when the same page
exists in two archives.

### Correctness and reader improvements

- Separate **inspected** articles in the step trace from **supporting** citations
  below the answer.
- Make article-read steps clickable and deep-link chat citations into the
  Knowledge view.
- Track iframe navigation so internal links, back, and forward stay inside the
  reader state.
- Explicitly block or label external links rather than allowing an offline
  reader to leave the archive silently.
- Add search pagination, useful snippets or lead extracts, filters, keyboard
  navigation, and friendly article-loading failures.
- Track iframe navigation so internal links, back, and forward stay inside
  the reader state, and add reader tests for that navigation.

### Next

**Kiwix catalogue browsing and resumable downloads** is the next branch. The
catalogue is at `opds.library.kiwix.org/catalog/v2/entries` (the
`library.kiwix.org` host 301s there), and it is Atom/OPDS **XML**, not JSON,
despite the "v2". Each entry's acquisition link is a `.meta4` (Metalink 4)
rather than the `.zim`, carrying the authoritative size, md5/sha-1/sha-256, and
a priority-ordered mirror list; the mirrors answer `Accept-Ranges: bytes`, so
resume works. `q` matches title words and not slugs (`q=stackoverflow` returns
nothing; `q=Stack Overflow` returns four). Downloads should land in a
**separate** root, not `knowledge_root` — the promise that Lunaschal never
writes into the archive directory is worth keeping literally true.

### Optional future capabilities

- automatic update discovery while keeping replacement user-confirmed;
- books, manuals, papers, and source-aware ranking;
- current versus historical/pre-2022 collections;
- bookmarks, reading history, annotations, and saved excerpts; and
- a small dedicated reranker if expanded mixed-source retrieval proves too
  large or slow for the main agent to select reliably.

None of these are required for the present milestone.

## Verification

Relevant automated coverage lives in:

- `backend/tests/test_knowledge.py` — classification over the real Kiwix
  filenames, registry sync/adoption/health, the fairness invariants (a 50-hit
  Q&A archive cannot starve an encyclopedia that sorts after it; a 30-archive
  DevDocs collection opens at most 8 per search; a disabled archive is never
  opened; one unreadable archive does not fail the search; four query variants
  cost one archive visit), the title-index path, and every route;
- `src/lib/knowledge.test.ts` and `src/components/Knowledge/Knowledge.test.tsx`;
- `backend/tests/test_delegate_chat.py`;
- `backend/tests/test_chat_compaction.py`; and
- `src/lib/agentSteps.test.ts`.

The real Simple English Wikipedia ZIM has been used as a smoke test for archive
discovery, full-text search, article reads, multi-query merging, exact title
ranking, and reader rendering.

**Manual smoke test for the federation**, which needs a second archive: drop
`devdocs_en_lit_2026-07.zim` (739 KB) beside the Wikipedia ZIM, then confirm it
lists under Documentation with an amber "title search only" badge, that a query
naming `lit` reaches it through the title index, and that a generic query still
returns Wikipedia first.
