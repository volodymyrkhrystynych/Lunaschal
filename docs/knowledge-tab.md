# Knowledge tab

**Status:** implemented on `feat/offline-knowledge-library` as a
Wikipedia-first offline reader and chat source.

This document records the current scope, the decisions behind it, and the work
that is deliberately deferred. Chat context retention and compaction are
covered separately in
[`chat-knowledge-and-compaction.md`](./chat-knowledge-and-compaction.md).

## Current product scope

The supported first version is one current Wikipedia ZIM stored on the user's
archive drive. The user downloads and updates the ZIM outside Lunaschal, points
Settings at the containing folder, and Lunaschal reads it in place.

The current target is intentionally smaller than the eventual 7 TB library:

- current Wikipedia only;
- no historical pre/post-2022 split;
- no in-app Kiwix catalogue, downloads, or updates;
- no books, papers, or mixed-source ranking yet; and
- no separate Kiwix HTTP server.

The implementation can discover several `.zim` files recursively, but search
quality is only considered supported for one primary Wikipedia archive. See
**Deferred roadmap** before treating that discovery as a federated library.

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
2. List archive metadata when no query is active.
3. Search the local ZIM index when the user submits a query.
4. Show matching titles and archive identity.
5. Load the selected archived page in a sandboxed iframe.

The Flask API is in `backend/routes/knowledge.py`:

| Endpoint                                          | Purpose                                                |
| ------------------------------------------------- | ------------------------------------------------------ |
| `GET /api/knowledge/config`                       | Return the configured directory and whether it exists. |
| `PUT /api/knowledge/config`                       | Validate and save a directory.                         |
| `GET /api/knowledge/archives`                     | Discover ZIMs and return archive metadata.             |
| `GET /api/knowledge/search?q=…`                   | Search the local archive index.                        |
| `GET /api/knowledge/archives/<id>/content/<path>` | Read an article or static resource.                    |

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

1. **Search is not federated fairly across multiple archives.** A large archive
   can fill the result limit before later filename-ordered archives are queried.
2. **Read sources are not yet distinguished from cited sources.** An article
   the agent inspected and rejected can still appear below the answer as if it
   supported the claim.
3. **Chat links do not deep-link into the Knowledge view.** They open the raw
   content endpoint instead of selecting the article in the reader.
4. **Reader navigation is minimal.** There is no app-level back/forward history,
   persistent selected article, bookmarks, annotations, or article table of
   contents.
5. **Manual search is literal and bounded.** It has no pagination, archive or
   language filters, search history, or LLM-generated query expansion.
6. **Search snippets may be empty.** The installed `libzim` binding currently
   yields entry paths rather than rich result objects for this archive.
7. **External hyperlink navigation is not explicitly intercepted.** Remote
   subresources and scripts are blocked, but the offline-only promise needs a
   browser test and an explicit policy for clicked external links.
8. **Frontend reader behavior has no dedicated component suite yet.** Backend
   archive/search/read behavior and main-agent integration are covered.

## Deferred roadmap

### Before adding non-Wikipedia archives

- Search every enabled archive for its own candidate quota, then globally rank
  and cap the merged set.
- Add per-archive enable/disable controls, source/language metadata, and clear
  corrupt or missing-index health states.
- Decide how duplicate articles and multiple snapshot dates should rank.
- Profile serial `libzim` search and archive caching against the intended drive.

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
- Add frontend tests for configuration, search states, selection, navigation,
  and broken archives; add backend tests for multi-archive fairness and link
  handling.

### Optional future capabilities

- Kiwix catalogue browsing and resumable ZIM downloads with checksum and disk
  space reporting;
- automatic update discovery while keeping replacement user-confirmed;
- books, manuals, papers, and source-aware ranking;
- current versus historical/pre-2022 collections;
- bookmarks, reading history, annotations, and saved excerpts; and
- a small dedicated reranker if expanded mixed-source retrieval proves too
  large or slow for the main agent to select reliably.

None of these are required for the present Wikipedia-only milestone.

## Verification

Relevant automated coverage lives in:

- `backend/tests/test_knowledge.py`;
- `backend/tests/test_delegate_chat.py`;
- `backend/tests/test_chat_compaction.py`; and
- `src/lib/agentSteps.test.ts`.

The real Simple English Wikipedia ZIM has also been used as a smoke test for
archive discovery, full-text search, article reads, multi-query merging, exact
title ranking, and reader rendering.
