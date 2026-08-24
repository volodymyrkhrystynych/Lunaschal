# Backfilling applications from email — a runbook

**There is no UI for this.** Both halves are Python modules with no route, no
button and no scheduler entry, so re-running them means opening a shell. That is
deliberate for now — see [Why there is no button](#why-there-is-no-button) — but
it means the procedure lives here rather than being discoverable in the app.

The problem it solves: the Jobs feature creates an `applications` row when you
apply _through_ it, so a search that predates the feature is invisible to it.
Linkage cannot help, because it attaches mail _to_ applications and there are
none. The first run of this found 5,596 classified job emails and zero
applications — a mailbox the linker had already walked 5,547 times and linked
nothing from.

| module                     | what it does                                              |
| -------------------------- | --------------------------------------------------------- |
| `backend/email/refetch.py` | re-pulls message bodies stored before `body_html` existed |
| `backend/jobs/backfill.py` | confirmation mail → `jobs` + `applications` rows          |

## Order matters

Run them in this order. The re-fetch first, because the backfill's coverage
depends on it: Indeed sends a plain-text part reading, in full, "Your
application has been submitted. Good luck!" — the employer's name is only in the
HTML. Before the re-fetch, 1,001 confirmations were unattributable; after it, 0.

```
refetch  →  plan (read-only, look at it)  →  commit  →  linkage sweep
```

### 1. Re-fetch the missing bodies

```bash
LUNASCHAL_NO_SCHEDULERS=1 .venv/bin/python -c "
import json
from backend.email import refetch
print(json.dumps(refetch.run(category='job_application', limit=5000, rate_per_second=200), indent=2))
"
```

Takes about 17 minutes for ~4,300 messages. **Run it in the background** — it
exceeds most foreground timeouts. It resumes safely: progress is the data
itself (a row with a non-empty `body_html` is done), so an interrupted run is
re-run with the same command and picks up where it stopped.

`rate_per_second` is clamped to Gmail's real ceiling of 50/s (250 quota units
per second ÷ 5 per `messages.get`), so passing 200 is harmless — it just means
"as fast as allowed". **Do not expect 50/s.** The measured throughput was ~4.2
messages a second, because the loop is serial and each round trip costs ~240ms;
the rate limit was never the binding constraint. Making this meaningfully faster
means concurrency, not a bigger number.

Watch it from another shell:

```bash
.venv/bin/python -c "
import sqlite3
db=sqlite3.connect('file:data/lunaschal.db?mode=ro', uri=True)
print(db.execute(\"SELECT COUNT(*) FROM emails WHERE category='job_application' AND (body_html IS NULL OR body_html='')\").fetchone()[0])
"
```

Reference run: 4,309 processed, 4,294 filled, 0 failed, 0 gone, 1,033s. The
remaining ~15 are messages that genuinely have no HTML part; they stay candidates
forever by design, because storing `''` would be indistinguishable from "not
fetched yet".

### 2. Preview — and read it properly

`plan()` writes nothing and can be run against a read-only connection, so it is
safe on the live database:

```bash
.venv/bin/python -c "
import sqlite3
from backend.jobs import backfill
db=sqlite3.connect('file:data/lunaschal.db?mode=ro', uri=True); db.row_factory=sqlite3.Row
p=backfill.plan(db)
print(f\"parsed {p['parsed']}/{p['scanned']} -> {p['toCreate']} applications / {p['companies']} companies\")
for i in p['items'][:20]:
    print(' ', i['company'][:30], '|', i['title'][:36], '|', i['parser'])
"
```

**An empty plan after a successful commit is correct, not a failure.** `parsed`
will still show ~1,585 while `toCreate` shows 0: the mail parses exactly as
before, and every candidate's `source_id` already exists. That is the
idempotency working. `toCreate` is only non-zero for confirmations that have
arrived since the last commit.

**Do not review it by recency.** `items` is sorted newest-first, and the first
live run looked clean in its first fifteen rows while carrying a company called
"Software" further down — which then absorbed 144 email links belonging to real
employers. Sort by what a bad row does, not by when it happened:

```bash
# after committing: which applications are hoovering up mail?
.venv/bin/python -c "
import sqlite3
db=sqlite3.connect('file:data/lunaschal.db?mode=ro', uri=True); db.row_factory=sqlite3.Row
for r in db.execute('''SELECT j.company, COUNT(l.id) links FROM applications a
  JOIN jobs j ON j.id=a.job_id LEFT JOIN job_email_links l ON l.application_id=a.id
  GROUP BY a.id ORDER BY links DESC LIMIT 15'''):
    print(f\"{r['links']:5}  {r['company']}\")
"
```

A healthy top of that list is a real employer in the teens (Scotiabank at 20 is
a bank that mails a lot). Anything in the hundreds, or any one-word industry
noun, is a parser bug — fix `_GENERIC_COMPANY` in `backfill.py`, reset, re-run.

### 3. Commit

```bash
LUNASCHAL_NO_SCHEDULERS=1 .venv/bin/python -c "
import json
from backend.db.connection import get_db
from backend.jobs import backfill
db = get_db()
addr = db.execute(\"SELECT email_address FROM email_accounts WHERE provider='gmail' LIMIT 1\").fetchone()
print(json.dumps(backfill.commit(db, applied_email=addr['email_address'] if addr else ''), indent=2))
"
```

Seconds, not minutes. Re-running is a no-op: `UNIQUE(source, source_id)` over
the `backfill:` prefix means an interrupted commit resumes rather than doubling
the pipeline.

`commit()` also reopens the `job_email_scans` verdicts itself — every one of
them was reached against an empty applications table, so left standing the whole
backfilled pipeline would sit at `submitted` forever. It reopens from the
_earliest_ application, not from now, because these span years and
`rescan_since`'s fourteen-day lookback around a current timestamp would leave all
but the last fortnight untouched.

### 4. Sweep

Nothing advances until the linker runs. The scheduler does this every tick, but
it does 200 at a time, so waiting for ~5,600 to drain naturally takes a while.
To force it:

```bash
LUNASCHAL_NO_SCHEDULERS=1 .venv/bin/python -c "
import time
from backend.jobs import linker
s=l=0; t=time.time()
while True:
    r = linker.run_linkage_sweep()
    if not r['scanned']: break
    s += r['scanned']; l += r['linked']
print(f'scanned {s}, linked {l} in {round(time.time()-t,1)}s')
"
```

Reference run: 5,598 scanned, 862 linked, ~180s. Pure string matching, no model
call — safe to run any time, and it does not need a llama slot.

## Resetting

Everything the backfill created is identifiable by its `source_id` prefix, so a
bad run is fully reversible:

```sql
DELETE FROM job_email_links WHERE application_id IN (
  SELECT a.id FROM applications a JOIN jobs j ON j.id = a.job_id
  WHERE j.source_id LIKE 'backfill:%');
DELETE FROM applications WHERE job_id IN (
  SELECT id FROM jobs WHERE source_id LIKE 'backfill:%');
DELETE FROM jobs WHERE source_id LIKE 'backfill:%';
DELETE FROM job_email_scans;   -- so the next sweep reconsiders everything
```

Clearing `job_email_scans` wholesale is the right move after a reset: the
verdicts were computed against applications that no longer exist. It costs one
sweep to rebuild.

**The re-fetch is not undone by this, and should not be.** It only ever filled
in `emails.body_html` where it was empty, never overwrote a `body_text` the
classifier had already read, and that data is good regardless of what the
backfill does with it.

## Reference numbers

From the run on 2026-08-23, useful for telling whether a future run went wrong:

| measure               | value                                                 |
| --------------------- | ----------------------------------------------------- |
| confirmations scanned | 1,944 (`job_status='sent'`)                           |
| parsed                | 1,585                                                 |
| applications created  | 1,299 across ~950 companies                           |
| skipped               | 359, of which 0 unresolved Indeed                     |
| pipeline after sweep  | 990 submitted / 141 ack / 147 rejected / 21 interview |
| email links           | 862                                                   |

A parse rate far below ~80% of scanned, or a company count far below the
application count, means the parsers have drifted against a changed ATS
template.

## Why there is no button

The backfill is a **one-off historical reconstruction**, not a recurring
operation. Once the applications exist, ordinary linkage keeps them current
from new mail, and running it again creates nothing (the source_ids already
exist). A button that does nothing on every press but the first is a worse
affordance than no button.

The re-fetch has a better claim to one, since `body_html` is still missing for
~18,000 non-job messages, but that is an Email-tab concern rather than a Jobs
one and nothing currently needs it.

What would change this: a second mailbox, or a period of not using the app long
enough for a mail backlog to build up again. At that point the honest shape is
a Settings → Jobs action with the progress registry the other long jobs use
(`RefetchProgress` is already in that shape), not a shell command in a doc.

## Things that will bite

- **`plan()` is safe on the live DB; `commit()` is not a preview.** They read the
  same mail and parse it identically, so what `plan()` showed is what `commit()`
  writes — but only if nothing re-classified in between.
- **The classifier's `job_status='sent'` is a filter, not a verdict.** It tags
  "Thank you for Creating an Account!" and job-alert newsletters as
  confirmations. What actually separates an application from mail that resembles
  one is that a `backfill.py` pattern matched.
- **A confirmation with no company is skipped, never stored half-formed** —
  `geo.py`'s rule about half a coordinate. The title is allowed to be empty
  (~130 applications have none, because the mail never said); the company is not.
- **Backfilled rows are `source='manual'`.** `jobs.source`'s CHECK cannot be
  ALTERed in SQLite without rebuilding the table, so provenance lives in
  `source_id` instead. Any query meaning "reconstructed" must test the prefix,
  not the source.
- **Two confirmations for one application collapse by (company, title)**, and
  `applied_at` is the earliest of them — a resend is not a second application.
  What this does _not_ catch is the same employer under two names: "ACV" and
  "ACV Auctions" remain two rows. Merging on prefix would also merge genuinely
  distinct companies, and `linkage.best_match` already refuses a close runner-up
  rather than guessing.
