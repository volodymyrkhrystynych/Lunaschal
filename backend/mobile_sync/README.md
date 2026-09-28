# Native sync protocol

`/api/mobile/capabilities`, `/sync`, and `/operations` use the existing app
authentication. Protocol version 1 projects only the columns in `registry.py`;
never add secrets, absolute file paths, or operational job state to a projection.

SQLite triggers capture every committed writer. Updates emit a revision only
when projected fields change. Streaming chat messages publish when finalized.
Recursive triggers are required for replacement/cascade deletion tombstones.
Projection changes rotate the server epoch and rebuild history during init.

Bootstrap fixes a revision watermark and pages the latest version of each row
at that watermark. Delta windows use the same immutable log. Cursors fix their
collection set. Clients commit each batch and its cursor together. HTTP 410
requires a new bootstrap while retaining local pending work.

Journal update/delete operations carry a stable ULID, server epoch, and base
revision. Mutation and durable receipt share one independent SQLite transaction.
Replaying an identical operation returns its receipt; reusing an ID for another
payload fails. A stale revision returns the current row without overwriting it.
Resolution requires a new operation ID against the current revision. A deleted
entry must be saved under a new entry ID if the user wants to retain that text.

## Maintenance

These commands change sync metadata in the configured database. Run them only
as part of an authorized server maintenance operation:

```sh
python -m backend.mobile_sync compact --keep-days 30
python -m backend.mobile_sync rotate-epoch
```

Compaction retains the latest baseline for every key (including tombstones) and
all newer revisions. Older cursors expire explicitly. Operation receipts are
retained, so delayed retries cannot repeat an acknowledged mutation.

After restoring a server database, rotate its epoch **before** accepting device
sync. Revision comparisons alone cannot detect a restored timeline which has
grown past an old cursor. There is no automatic restore detector or compaction
scheduler yet. Neither command deletes domain records or media.

Run `backend/tests/test_mobile_sync.py` and `test_seed_test_db.py` after changing
the protocol/schema. Native transaction and conflict tests live in
`apple/LunaschalCore/Tests/LunaschalCoreTests/ReplicaTests.swift`.
