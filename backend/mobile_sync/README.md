# Native sync protocol

`/api/mobile/capabilities`, `/sync`, and `/operations` use the existing app
authentication. Protocol version 1 projects only the columns in `registry.py`;
never add secrets, absolute file paths, or operational job state to a projection.

SQLite triggers capture every committed writer. Updates emit a revision only
when projected fields change. Streaming chat messages publish when finalized.
Recursive triggers are required for replacement/cascade deletion tombstones.
Projection changes rotate the server epoch and rebuild history during init.

Bootstrap fixes a revision watermark and pages the latest version of each row
at that watermark. Delta windows use the same immutable log and the same rule:
each record's latest version inside the window, never the versions it passed
through on the way (a delta once sent every one, and the device wrote each only
to keep the last). Cursors fix their collection set. Clients commit each batch
and its cursor together. HTTP 410 requires a new bootstrap of that scope while
retaining local pending work; other scopes' cursors are unaffected.

`POST /sync/status` takes `{"cursors": [...]}` (up to 16) and answers, per
cursor, `{"changed", "resetRequired"}` without sending any records. A device
holds several scopes and almost always finds nothing new, so it asks this once
instead of fetching an empty page per scope. `changed` is true partway through
a window or when any of the scope's collections has a row past the cursor.
Capabilities advertise it as `"syncStatus": true`.

Journal update/delete operations carry a stable ULID, server epoch, and base
revision. Mutation and durable receipt share one independent SQLite transaction.
Replaying an identical operation returns its receipt; reusing an ID for another
payload fails. A stale revision returns the current row without overwriting it.
Resolution requires a new operation ID against the current revision. A deleted
entry must be saved under a new entry ID if the user wants to retain that text.

Book snapshots include safe folder IDs, site tags, source domain, and latest
chapter activity. Relationship/chapter triggers publish immutable replacement
book snapshots, including unfiling/tag deletion. Projection changes rotate the
epoch, so existing clients must bootstrap again.

Bookmark create/delete operations share the journal operation receipt transaction.
Favorites and Continue-reading bookmarks use the existing desktop table and
fractional chapter positions. A Continue replacement supplies its previous
bookmark ID/revision; a stale offline choice conflicts instead of replacing a
newer desktop choice. Delete also checks the bookmark revision. Replays return
the original receipt without recreating a later-deleted bookmark.

## Maintenance

These commands change sync metadata in the configured database. Run them only
as part of an authorized server maintenance operation:

```sh
python -m backend.mobile_sync compact --keep-days 90
python -m backend.mobile_sync rotate-epoch
```

Compaction retains the latest baseline for every key (including tombstones) and
all newer revisions. Older cursors expire explicitly. Operation receipts are
retained, so delayed retries cannot repeat an acknowledged mutation. It runs by
itself once a day, right after the morning briefing in the briefing window
(`backend/briefing_scheduler.py`, `SYNC_HISTORY_DAYS = 90`), and also on days
the briefing is turned off. Ninety days because a device that hasn't synced in
longer must bootstrap again, which for downloaded library text means a Wi-Fi
re-download; the app starts that download itself once it sees the 410.

After restoring a server database, rotate its epoch **before** accepting device
sync. Revision comparisons alone cannot detect a restored timeline which has
grown past an old cursor. There is no automatic restore detector. Neither
command deletes domain records or media.

Run `backend/tests/test_mobile_sync.py` and `test_seed_test_db.py` after changing
the protocol/schema. Native transaction and conflict tests live in
`apple/LunaschalCore/Tests/LunaschalCoreTests/ReplicaTests.swift`.

## Media downloads

`GET /api/mobile/media?collection=...&after=...` enumerates active local media
with record IDs, byte sizes, SHA-256 hashes, and versioned file URLs.
The allowlist is independent of the text projections. No stored path is exposed;
canonicalized paths pass existing storage-root guards. Archive videos remain
excluded even if a legacy row points to a local file. Missing files remain in
the manifest as unavailable records.

File requests require the manifest's hash. A changed file returns 412; clients
refresh the manifest and start a new partial file. HTTP byte ranges and strong
ETags support resume, and clients verify the final hash before publishing a
downloaded copy. Enumeration is a rescan, not the immutable record-change feed:
files can change independently of projected columns. Repeat it for each bulk
download pass. The hash cache is bounded and keyed by file stat metadata.

`backend/tests/test_mobile_media.py` covers ranges, replacement, missing files,
archive exclusion, path confinement, and manifest validation. The `fics` media
collection serves imported PDF books from their canonical ID-scoped `book.pdf`;
non-PDF entries remain unavailable, and symlinks cannot cross book identities or
storage roots. Clients negotiate `mediaCollections` from `/capabilities` before
requesting new collection types. Full newspaper PDFs, inline chapter images,
optional archive pins, and ZIM are not included yet.
