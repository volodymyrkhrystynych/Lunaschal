# Fanfic library (`backend/routes/fanfic.py`, `backend/fanfic/`, `src/components/Fanfic/`)

Personal fanfiction library + reader ("Library" in the UI). Imports from XenForo forums (SpaceBattles / Sufficient Velocity / Questionable Questing) by scraping threadmark reader pages — `xenforo.py` is a **pure parser** (no network/DB; tests feed fixture HTML), `download.py` streams chapters into the DB one reader page at a time (resumable; in-memory progress registry; 2 s request delay; browser UA + per-domain cookies from `site_cookies` for Cloudflare). Also imports epub/docx uploads and stores PDFs. Chapters keep sanitized HTML + plain text (FTS). Per-fic: folders (ordered), site tags, per-chapter read tracking, last-read position, rating/review, update checking (`check-updates` / `refresh-alerts` set `update_pending`; a single drain worker walks the flags one fic at a time). Journal entries can reference fics/chapters (`journal_entry_fic_refs`) — reading commentary shows up in the Journal feed and deep-links back into the reader.

**FanFiction.net, AO3 and Patreon** use pure parsers in `sites.py` and the jobs in
`collections.py`. Library → Import → From website accepts a single story/work/post;
My collections imports FFN favorite/followed stories, AO3 bookmarked works and work
subscriptions (requires the account username), or accessible text posts from the
Patreon feed. Direct HTTP collection scans require a saved browser session in
Settings → Fanfic site cookies. FF.net browser mode uses the Chrome extension's
signed-in session instead. No passwords or cookies are returned by the status APIs.
AO3 author/series subscriptions and external bookmarks are not expanded; Patreon
media and attachments are not downloaded. Locked posts are skipped and counted;
unreadable stories/posts fail visibly instead of saving a login page or teaser.

Collection pagination and counters live in `fanfic_collection_scans`. Every page
queues canonical `(site, thread_id)` work identities through the existing persistent
`fics.update_pending` queue, then checkpoints its next URL. Retry resumes a stopped
scan; rerunning a completed scan starts at page one and skips existing works. A
crash during a page can change its imported/already-present counts on replay, but
cannot duplicate stories. Pending scans and interrupted downloads for these sites
resume at startup, within the `LUNASCHAL_NO_SCHEDULERS` boundary.

**A page fetch that fails for nobody's reason defers the scan; it does not end
it.** AO3 sits behind a CDN that intermittently answers 525 (edge SSL handshake
failed) or 502/504 for a URL that works seconds later, and a 525 used to fall
straight past `_fetch_serial`'s backoff — `RETRY_STATUSES` now covers the whole
5xx edge block alongside 403/429 — and then past `run_scan` into `status='error'`,
stranding a checkpoint that only needed asking again. A scan whose failure
`download.is_transient` recognizes (a timeout, a dropped connection, one of those
statuses outliving the in-fetch retries) stays `pending` with `retry_after` set
from `RETRY_SCHEDULE`, and the worker's one `_RUNNABLE_SQL` gate is what stops
that from being a busy loop against a site that just failed to answer — the same
job `fanfic_site_limits` already does for `DeferredDownload`, which is why that
older path can get away with a bare `pending`. **Running out of schedule entries
is what makes it a real error**, so a site that is genuinely gone stops being
retried; `attempts` resets on every page that comes back, so a long walk meeting
one flaky page every few hundred never accumulates its way there. A challenge
page and a parse failure are not transient — they want cookies and a fix, not a
timer. Starting the scan again clears the deferral: asking is asking for now. The shared fetch
lock serializes requests and their delay. New sites only follow same-host HTTPS
redirects so session cookies cannot be forwarded to unrelated hosts. Update adds
missing chapters; Deep refreshes existing chapters in place, preserving their IDs,
read markers and bookmarks. The source CHECK migration rebuilds `fics` with foreign
keys temporarily disabled and verifies references before committing.

**Your own tags on a site become folders** (`personal_tags.py`, the one place both sources land). Two sites carry a filing the library would otherwise have to be given by hand: XenForo **bookmark labels** on `/account/bookmarks` (`xenforo.parse_bookmarks` → `download.run_bookmark_scan`, checkpointed in `fanfic_bookmark_scans`, triggered by `POST /api/fanfic/scan-bookmarks/<domain>` from Settings → Fanfic site cookies) and AO3 **bookmark tags** on the bookmarks page the collection scan already fetches (`sites._bookmark_tags` → `WorkRef.tags` → `collections.queue_work`, so the existing My-collections scan is the manual re-sync). A bookmarked thread not in the library is queued for import like a watched one, and its folders are written _before_ the download, so a fic whose download fails is still filed where you put it. The site's _public_ tags are not this — those stay `fic_site_tags`, shown as tag pills; folding them in would bury the folder bar under fandom names.

Three rules make the sync safe to re-run. It reuses an existing folder of the same name **case-insensitively** rather than making a twin, and never relabels one — a folder you made by hand stays `origin='manual'` though a label shares its name. It may only delete what it filed: `fic_folder_items.origin` records who put each fic in each folder, filing by hand **upgrades** an imported membership to manual (`add_fic_to_folder`'s `ON CONFLICT … DO UPDATE`), and the sync's delete is scoped to `origin='import'`. And **an empty tag list is a no-op, not a clear-out** — an unlabelled bookmark and a parse that found nothing are the same bytes from here, so a markup change on the site can leave a stale folder but can never quietly unfile the library. Deleting an imported folder in the UI is allowed and says so: the label still exists on the site, so the next sync makes it again.

The one unverified piece is XenForo's bookmark-page markup, which differs by theme. Labels are found by **href** (`/account/bookmarks?label=…`) rather than by class, with `.bookmarkLabel`/`a.tagItem` as a fallback; a bookmark on a _post_ resolves to its thread, and two bookmarks in one thread merge into one fic carrying both label sets.

**The reader's Commentary panel has two halves that finish differently.** Typed commentary posts its text (`journal.createFromVoice`) and links it in a second call. The **microphone is the Journal button's contract instead — stopping the recording is the save**: the clip goes to the durable store, uploads as a journal entry carrying `ficId`/`chapterId` (`captureFicCommentary` → `POST /api/journal/recordings`), and the transcript, the polish and the title arrive on that entry afterwards. It used to transcribe in the browser and post the text, so the audio existed only in memory and a failed transcription lost the commentary outright. The chapter is captured with the first chunk and stored beside the audio, because W/S walks the reader on while a thought is still being spoken — resolving it at upload time would file the entry under whatever chapter was open by then.

**Update checks come in two tiers, because an edit is invisible from outside the post.** XenForo raises no alert when an author revises an existing chapter and leaves the threadmarks index untouched, so nothing about the fic looks different until you re-read the post itself.

**FF.net pacing** is owned by `pacing.py`: ten minutes by default between page
requests, including redirects/retries. `fanfic_site_limits` persists the next
request time and configurable `request_interval` across restarts. Library and
import controls let the user save the interval in minutes (15 seconds to one day).
Changes recalculate the current wait without clearing pauses or server cooldowns.
Library and import controls offer Pause / Resume;
manual pauses use the persistent site pause and stop before the next request,
while an in-flight request can finish. Waiting checks for a pause every second
and releases the shared fetch lock so other sites can fetch during the interval.
The status shows the earliest next request time. Cooldown, pause and rate-limit
count also persist. HTTP 429 honors
Retry-After (seconds or an HTTP date); absent/invalid values use a 15-minute
exponential cooldown capped at one day. Cloudflare challenges pause indefinitely
until the Library banner's Resume button is used. Resume cannot shorten a server
cooldown. Deferred work retains its queue flag, deep-update intent and saved
chapters; collection scans retain their current page. Other sites remain eligible.
A five-second recovery loop restarts eligible queues after cooldown or application
restart, and is disabled by `LUNASCHAL_NO_SCHEDULERS`.

**FF.net can use the user's browser** (`browser.py`, `extension/ffn.*`,
`extension/lib/ffn.js`). It is opt-in: choose Browser session in the Library,
or Connect in the extension's FF.net downloads control tab. Direct HTTP stays
available for existing setups; browser mode **never silently falls back to it**.
The ordinary Chrome/Chromium profile owns the session and the user solves any
challenge or login in a dedicated site tab. Both that tab and the extension
control tab must stay open. Backend traffic uses the extension's existing
service-worker API client and authentication; no cookie export is involved.

`fanfic_browser_requests` is the durable handoff, scoped to a fic or collection
scan with cascading deletion. A cache miss defers the existing worker instead
of holding a thread/HTTP connection while a browser is absent. The two queues
exclude FF.net while a page is outstanding or the browser heartbeat is stale;
other sites still run. Each allowed top-level navigation reserves the existing
pacing deadline. Browser-managed redirects, subresources, and manual challenge
interactions are not individually paced. HTTP 429 reported by the extension's
main-document header observer preserves Retry-After through the same cooldown.

Only one live controller may claim pages. Claims carry an attempt id, so a late
reply from before a retry cannot satisfy the next attempt. Polling an assigned
request does not navigate again. A challenge/login/unreadable page remains
blocked until the user chooses Continue (capture the current page) or Retry
(a new paced navigation); even takeover by a new controller does not reload a
blocked page automatically. Closing a tab, losing the server, or restarting
keeps durable pages and the original queue flags. Deep intent stays on an
active fic until completion. Responses are checked against the requested
story/chapter or collection page, size-limited, validated by the existing
parsers, and consumed by the existing sanitizer/storage path. Cached pages are
cleared after each collection checkpoint or atomically with fic completion.

The extension controller lives in a normal extension tab: an MV3 worker going
idle cannot kill its timer. Background-tab throttling can lengthen the interval.
Browser status expires after three minutes without polling. See
[`extension/README.md`](../../extension/README.md#ffnet-browser-downloads) for setup.

FF.net collection scans preserve `fics.source_favorited_at` and
`source_followed_at` separately. Account-list addition dates are stored at UTC
midnight and displayed in UTC to preserve the calendar date. The six-column
account table puts Updated before Added (see the
[ffn-parser source embedded in FanFiction Enhancements](https://greasyfork.org/en/scripts/491097-fanfiction-enhancements/code)).
Public profile publication/update timestamps are never used as addition dates.
Missing dates remain NULL; rescanning enriches existing canonical story rows
without requeueing completed downloads or erasing previously saved dates.
Library cards show both source dates independently of the local import date;
description refreshes do not overwrite this history.

FF.net chapter selectors may omit option end tags. Read only each option's
direct text: `html.parser` nests later options, so recursive text concatenates
the remaining titles. Successful ordinary update checks refresh saved chapter
titles from the first page's selector, repairing older imports without fetching
existing chapter bodies or changing their IDs and reading state.

- A **cheap** check looks only for chapters we don't have. It diffs the threadmarks index's post ids against the stored ones and resumes at the reader page holding the first missing chapter — one index fetch per ~50 threadmarks per category, and no reader fetch at all when nothing is missing. Three things about it are load-bearing, and all three were bugs:
  - **`Statistics (N threadmarks)` is never used to skip a category.** It counts a different population than our rows do — threadmarks get recategorised, renamed and deleted on long threads — so the two drift apart, and every count-based shortcut fails in one of two ways. `count <= rows` latched a fic shut permanently the moment the site's count fell below ours (`test_check_updates_survives_the_site_losing_a_threadmark`): this is the root cause of recently-updated fics never downloading, and a category losing a couple of _non-chapter_ threadmarks is enough to trigger it. `count == rows` then still agreed a category was current when it had swapped two threadmarks for two others (`test_check_updates_sees_swapped_threadmarks_at_an_unchanged_count`). Post ids are the only comparison that can't be fooled.
  - **The resume page comes from the index position, never from our row count.** Count arithmetic overshoots whenever there's a gap, so a chapter missing from the middle pushed the walk past the very page holding it and stayed missing forever.
  - Chapters the site un-threadmarks are **kept**, not deleted — we downloaded them, and the site dropping a threadmark isn't a reason to destroy the reader's copy.
- A **deep** check walks every reader page and compares each post against the saved chapter. `fic_chapters.edited_at` holds XenForo's "Last edited" timestamp — parsed from `.message-lastEdit`, which sits in the same `<article>` the body does, so it costs no extra request. A changed chapter is rewritten **in place**: `position` is never touched, or a typo fix would reshuffle reading order and the last-read pointer.
- **Deep only ever runs when asked** (`{"deep": true}`, the Deep button; `fics.deep_pending` carries the request to the drain worker). There is deliberately no cadence and no auto-escalation: authors revising already-published chapters is rare, so re-walking every fic on a timer would spend far more requests than it recovers.
- **`edited_at` is left NULL by the migration on purpose.** A post with no edit notice also parses to `None`, so unchanged chapters compare equal and a first deep scan doesn't rewrite the whole library.
- **`refresh-alerts` no longer skips a fic for being fetched more recently than its alert.** That comparison assumed an alert is the only way a thread changes; with edits raising none, "checked since the alert" regularly meant reporting a fic current while a revised chapter sat unread.
