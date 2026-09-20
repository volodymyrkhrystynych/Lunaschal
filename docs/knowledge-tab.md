# Knowledge tab

**Status:** implemented as a federated offline reader and chat source over
any number of Kiwix ZIM archives.

This document records the current scope, the decisions behind it, and the work
that is deliberately deferred. Chat context retention and compaction are
covered separately in
[`chat-knowledge-and-compaction.md`](./chat-knowledge-and-compaction.md).

## Current product scope

The library is one or more current ZIM archives on the user's archive drive.
The user points Settings at the containing folder, and Lunaschal reads them in
place — and, since the catalogue browser shipped, can also fetch new ones into
that same folder from the Kiwix mirrors.

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
- **Some archives have no fulltext index, and the catalogue is not a reliable
  guide to which.** `Searcher` returns nothing at all for such an archive;
  they answer through `libzim.suggestion.SuggestionSearcher`, which queries
  the title index — so a no-fulltext archive is a normal archive here, not a
  broken one, and the UI says "title search only" in amber rather than
  flagging an error.

  **Correction to the original research.** This was first written as "every
  DevDocs ZIM Kiwix publishes is `_ftindex:no`", taken from the catalogue,
  where all 231 DevDocs entries do carry that tag. The archives themselves
  disagree: `devdocs_en_sinon_2026-08.zim` and `devdocs_en_qunit_2026-07.zim`
  were both downloaded and opened, and both report `has_fulltext_index ==
True` from libzim while carrying no `_ftindex` tag of their own at all
  (their entire `Tags` is `devdocs;sinon` / `devdocs;qunit`). The flag exists
  only in the library server's generated metadata and is wrong there. Nothing
  in the reader depended on the wrong version — `registry._probe` has always
  asked libzim rather than the catalogue — but the **catalogue browser** can
  only show what the catalogue claims, so it is worded as a claim
  ("catalogue says: no fulltext index") and re-derived from the file once it
  is on disk. 7 of the 181 Stack Exchange entries are tagged the same way and
  have not been checked against their archives.

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
value is empty. ZIM files are discovered recursively, and an archive already in
the folder is never modified.

**The folder is no longer read-only to Lunaschal.** The reader half of this
feature promised never to write into the archive directory at all; the
downloader retires that promise deliberately, on the user's decision to have
one root rather than two. A finished download is renamed into place there, so
it joins the library with no copy and no second path to configure. What
replaces the promise is a narrower one: _an archive already in the folder is
never written to, renamed, or deleted._ Only `<name>.zim.part` files the
downloader created itself are.

That introduces a state the reader never had to consider — the root can be
unwritable — and `download.root_state()` distinguishes four cases because each
is a different thing to go and fix: `unset`, `missing` (the drive is
unplugged), `readonly` (an `ST_RDONLY` mount, wanting fsck) and `permissions`
(a read-write mount this user cannot write to, wanting `uid=`/`gid=` in fstab,
which is what exFAT needs since it stores no POSIX ownership). `os.access`
answers False for the last two alike, which is why the split exists — the same
split `backend/routes/backup.py` makes.

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

## Getting archives

`backend/offline_knowledge/catalog.py` reads the Kiwix catalogue and
`download.py` fetches from it; both are reached through the existing
`knowledge` blueprint. Five things about the upstream service are not
guessable and each one shaped the code:

- `library.kiwix.org/catalog/v2/…` **301s to `opds.library.kiwix.org`**, and
  the response is **Atom/OPDS XML, not JSON**, despite the "v2". Parsed with
  stdlib `ElementTree`; BeautifulSoup is the HTML tool.
- The acquisition link is a **`.meta4` (Metalink 4), not the `.zim`** —
  fetching the `href` as if it were the archive gets a 2 KB XML file with a
  `.zim` name. The Metalink carries the authoritative size, md5/sha-1/sha-256,
  a sha-1 piece list, and a priority-ordered mirror list. Its `<size>` and the
  OPDS `length` **disagree** (361379 against 361472 on the entry this was
  verified against) and the Metalink wins: it is what the mirrors serve, and
  reserving disk against the other number is how a transfer fails at 99%.
- **`/catalog/v2/entry/<uuid>` is unusable.** It answers 200 with a bare
  `<entry>` root that uses an undeclared `dc:` prefix — not well-formed XML.
  One archive is re-resolved through `/entries?name=<slug>` instead; the slug
  is not unique (`wikipedia_en_all` matches three flavours), so the uuid picks
  between them.
- **`q` matches title words, not slugs**: `q=stackoverflow` returns nothing
  where `q=Stack Overflow` returns four. The search box says so.
- `lang=` takes ISO-639-3 and **does** narrow (`eng` → 1301, `fra` → 517). An
  earlier note here said it did not; that came from testing it against
  `category=stack_exchange`, all 181 entries of which are English.
  `category=` has 16 values and **there is no `devdocs` category** — DevDocs
  is reachable only as `tag=devdocs` (231 entries).

An error page is not an empty library: `<html>503</html>` is perfectly
well-formed XML, so the parser checks the root element and raises rather than
reporting zero results.

### How a download survives things

One at a time, in queue order — the mirrors are donated bandwidth and the
drive is one spindle. Then, in order:

1. The destination is checked (the four states above) and `shutil.disk_usage`
   must show `size × 1.05` free, **before a byte is fetched**.
2. Every candidate mirror goes through `backend.research.web.assert_public_url`
   — the catalogue chooses these hosts, so this is the SSRF shape, not
   `backend/repos/git.py`'s, whose threat is git's own transports.
3. Bytes land in `<filename>.zim.part` with a `Range` request, checkpointed to
   `downloaded_bytes` every 16 MiB rather than every chunk. **A 200 answer to
   a Range request means the server ignored it**, and is treated as "start
   over": appending a whole file to a partial one writes a second copy into
   the middle of the first, and nothing notices until the checksum fails.
4. **A resumed `.part` is verified against the piece hashes first**, and
   truncated back to the last piece that matches. This is the one genuinely
   non-obvious part. A mirror may have rotated to a newer build between
   sessions; without the piece check, appending to yesterday's bytes is only
   caught by the whole-file hash, by which point the alternative to keeping
   the file is fetching 107 GB again. Re-reading the part at 4 MiB per sha-1
   is a couple of minutes.
5. `verifying` is a status of its own, because a transfer assembled across two
   sessions cannot carry an incremental hash — the finished file is read back
   once.
6. **On mismatch the `.part` is kept and nothing is renamed.** A half-good
   file that is obviously a `.part` can be resumed or deleted; the same bytes
   under a real `.zim` name are a corrupt archive the reader will open.
7. On success: rename in place, `registry.sync(force=True)`, and write
   `expected_size` — which is what makes the `truncated` health state
   reachable.

`_reset_stale_knowledge_downloads` parks an interrupted row at **`paused`, not
`error`**, keeping `downloaded_bytes`: the bytes on disk are the point of a
resumable transfer. It does not restart anything either — deciding on its own
to pull the remaining 90 GB is not a startup path's call. Resume is a button.

### Deliberately not built

**No "a newer build is available" check.** The catalogue's `<name>` is the
undated slug and the filename carries the date, so the comparison is already
possible from stored data; it wants a decision about what happens to the
superseded file, and that is a separate change.

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
   a `kind`, but not paged, and has no LLM-generated query expansion. (The
   _catalogue_ browser does have a language filter; the search over installed
   archives does not.)
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
9. **The catalogue browser does not page.** It asks for the first `count`
   entries of a filter and shows how many matched, so narrowing is done with
   the search box and the pickers rather than by scrolling. `start` is
   plumbed through and unused.
10. **Nothing reports that an installed archive has a newer build.** See
    "Deliberately not built" above.
11. **A resumed download re-reads the whole `.part` to verify its pieces.**
    On 107 GB that is a couple of minutes before the first new byte. It is
    the right trade against re-fetching, but it is not free and there is no
    progress shown for it.

## Deferred roadmap

### Done — kept here as the record of what unblocked the new sources

Fair per-archive quotas with a global merge, per-archive enable/disable,
source/kind metadata, health states, and the title-index path all shipped on
`feat/knowledge-federated-search`. Kiwix catalogue browsing and resumable,
checksum-verified downloads shipped on `feat/knowledge-catalogue`. What is
still open from that list: how duplicate articles and multiple snapshot dates
should rank when the same page exists in two archives.

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
- `backend/tests/test_knowledge_catalog.py` — catalogue and Metalink parsing
  against checked-in fixtures captured from the live service
  (`backend/tests/fixtures/kiwix/`, so no test needs the network), the four
  unwritable-root states, piece verification and rollback, resume with a
  `Range` header, a server that ignores `Range`, a checksum mismatch keeping
  the `.part` and publishing nothing, the disk-space and SSRF refusals, the
  restart reset, and the routes;
- `src/lib/knowledge.test.ts`, `src/lib/knowledgeDownloads.test.ts`,
  `src/components/Knowledge/Knowledge.test.tsx` and
  `src/components/Knowledge/CatalogPanel.test.tsx`;
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

**Manual smoke test for the downloader**, since every unit test above runs
against a fixture: queue `devdocs_en_sinon_2026-08.zim` from the DevDocs
shortcut — 361 KB. Confirm the file lands in the archive folder, that
`sha256sum` gives
`2689ed4abaaeaf766bf9541596e5d59ea8065d0c0e0e0dcaf48e86e4d2087769`, and that
it appears in the archive list as Documentation without a manual rescan —
**and note that it lists as healthy, not "title search only", even though the
catalogue tagged it `_ftindex:no`**; that disagreement is real and is
explained under "Why libzim is embedded". Then kill the process part-way
through something larger,
restart, and confirm the row reads `paused` with its bytes intact and that
Resume finishes it.
