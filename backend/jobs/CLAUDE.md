# Job applications

Discovery → triage → tailored resume → application → email trail → deletion.
The Jobs tab (`src/components/Jobs/`) is the UI; `backend/routes/jobs.py` is
thin and every judgement call lives in a pure module here. Design record, the
decisions the build settled, and what is deliberately _not_ built — including
the browser extension and the thresholds that are still guesses:
[docs/jobs-tab.md](../../docs/jobs-tab.md).

**The feature is split across two devices on purpose.** The phone is where
judgement happens — scroll the feed, read what you are missing, Queue or
Dismiss — and the desktop is where mechanical work happens: review the
generated resume, fix it, send it. That split is why `POST /<job_id>/queue`
writes one row and returns instead of tailoring inline: tapping Queue on a bus
is a decision, not a request to wait on a model.

## Getting a profile in the first place

`resume_import.py` reads an existing `.docx` (or pasted text) into the profile,
because the profile is the root of everything here and typing it was the only
way to create one. Nothing works before it exists: `tailor.py` has no bullets
to select and `keyword_report` has no vocabulary, so **every posting scores
NULL and the feed cannot sort at all**.

**The model never writes bullet text.** The document becomes numbered lines and
the schema's `bulletIndexes` is bounded to that list, so there is no field in
which prose could be returned; bullet text is reconstructed verbatim from the
source lines. The bound matters more here than in tailoring — a tailored bullet
is reviewed once and sent, while an imported one _becomes_ `profile_bullets`,
which every future resume is generated from and which the anti-fabrication
guarantee treats as fact. A quiet rewording at import is permanent.

Company, title and dates stay plain strings: a resume line is routinely
`Acme Inc · Senior Engineer · 2021–Present`, three fields in one line, which no
index can address.

Import **previews and commits separately**, and the commit **appends, never
replaces** — it also fills only blank contact fields, so a phone number the
user corrected by hand survives a re-import. `.docx` goes through **mammoth**
(already a dependency via `backend/fanfic/docx.py`) because it maps Word's list
paragraphs to `<li>`, which separates accomplishments from headings without
guessing. Lines starting with `•` are treated as a _hint_, since plenty of
resumes fake lists with characters.

## The three guarantees

**A tailored resume cannot invent experience.** `tailor.py` hands the model a
_numbered list of real bullets_ and a schema whose `index` is bounded to that
list. llama-server compiles the bound to a GBNF grammar, so an out-of-range
index cannot be decoded, not merely rejected afterwards — the
`backend/ai/idea_assessment.py` trick. Bullets are relational rows
(`profile_bullets`) rather than a prose blob precisely so they can be addressed
this way. `clamp()` re-applies every bound anyway, because the output ends up
on a document with the user's name on it.

**Keywords are computed, not generated.** `keywords.py` works out which of the
posting's terms the profile can evidence. Only those reach the model, as an
`enum` on the `emphasis` field; the missing ones are shown to it explicitly as
forbidden. Matching is vocabulary-driven (BASE_TERMS ∪ the user's own skills,
so it grows with the profile) with longest-match-wins over spans — word
boundaries alone let `c` match inside `c++`, and tightening them would break
`Python.` at the end of a sentence.

What none of this prevents is inflation _inside_ a rewrite ("helped with" →
"led"), so `content` stores `original` beside every `text` and the UI shows the
change. The last check is the person whose name is on it.

**A rejection cannot un-reject an application.** `linkage.advance_status` is
monotonic along `PROGRESS_RANK`, so a confirmation email that syncs late cannot
walk `interview` back to `acknowledged`, nothing reopens a `rejected` without a
human, and `withdrawn` is never overwritten.

## Discovery and the feed

**The board gives you everything, so something has to filter.** The adapters
fetch every open posting a board has — a company with 400 openings puts 400
rows in `jobs`. There is no title filter at the source and no way to ask for
one: Greenhouse, Lever and Ashby take a slug and nothing else. Only Adzuna
accepts a query (`what`/`where`), and that is the aggregator filtering server
side. So the feed was a whole job board with an ordering applied, and the
filtering happens here instead.

### The triage cascade

Three layers, each only passing on what it cannot decide:

| layer            | cost                   | file                   |
| ---------------- | ---------------------- | ---------------------- |
| title gate       | free, every tick       | `triage.py` (pure)     |
| body fetch       | Adzuna rows only       | `ingest.fetch_posting` |
| judge + condense | one model call, cached | `ai/job_triage.py`     |

**The gate is exclusion-only and fails open.** An inclusion whitelist for
"developer" and "AI" drops exactly the tangential roles worth seeing — Forward
Deployed Engineer, Solutions Architect, Developer Advocate. An exclusion list
drops none of them. Anything the gate is unsure about survives to the layer
qualified to judge it, the same instinct that makes `urlmatch.py` resolve
ambiguity to None.

Its three tiers exist because rescue-first alone is not enough. A **hard**
phrase settles a title outright; a **software signal** rescues an ambiguous one
("Clinical Data Scientist", "Warehouse Automation Engineer"); a **soft** phrase
rejects only what the signal did not vouch for. A phrase belongs in the hard
tier **only when the phrase itself collides with a software signal** — "Security
Guard" contains 'security', "Data Entry Clerk" contains 'data'. Putting anything
else there is how `seo` briefly rejected "Sr. Full Stack Engineer (SEO)", a
posting the user had actually applied to.

**Judging and condensing are one model call, not two.** Both need the whole
posting read, and that prefill is the expensive part. Measured at 3–8 seconds
against a 10,000-character description, which is what makes judging every new
posting affordable at all — and it is why there is no title-only model pass
between the gate and the judge: at that speed the extra layer would only add a
chance to discard a tangential role on the weakest available signal.

**What is computable is computed first and handed over as fact.** `triage.py`
extracts the stated seniority and the years of experience demanded, and flags
the case where they disagree — a "Junior" title wanting ten years. That is a
regex result, not an opinion, so `normalize_result` adds the flag even when the
model failed to raise it. `missingMustHaves` is `enum`-bound to the terms
`keyword_report` already returned as missing, so the model cannot invent a
requirement the posting never stated.

**Two conditions decide whether a posting is worth a model call at all**, both
learned from the live database, where 1,296 of 1,370 pending rows were neither:
it must have a body (the backfilled rows were rebuilt from confirmation mail,
which never carried the posting — judging one means judging its title), and
nothing may have been applied to it yet (those have left triage, and the feed
excludes them for the same reason). `_TRIAGEABLE` is one constant shared by the
selector and the counter, so the status panel can never disagree with the
worker.

**A rejection is a state, not a delete, and not `dismissed`.** `dismissed` means
_the user_ said no; conflating the two destroys the record of who decided.
Rejected rows stay in the table and are reachable at `GET /filtered`, because a
filter that discards job opportunities on a rule the user never sees has to be
reviewable — the lesson the backfill's bogus "Software" company taught, where
sampling by recency hid a row that had absorbed 144 email links.

**The feed shows `kept` and `pending`; only `rejected` is excluded.** So with
the model off, or the backlog undrained, the feed behaves exactly as it did
before triage existed rather than silently emptying.

### The score, and what the model is allowed to move

`keywords.py` computes coverage deterministically at sync time, for every
posting, with no model call — so a 200-job sync is free and the feed is sorted
the moment it lands.

Triage **changed one thing** about the rule that the model never moves the sort
order, and it is worth being precise about what: the model now chooses a coarse
**bucket** (`strong`/`possible`/`stretch`) that groups the feed, and decides
what is in the feed at all. It still does not order anything — within a bucket
the order is the deterministic keyword score. A bucket is stable between
refreshes in the way the original rule was protecting; a model-produced 0–100
score would not be.

`ai/job_match.py` is untouched and still narrates one posting on demand when you
open it, answering "what should this application lead with" — a different
question from "should this be on the screen", and only worth asking once you are
already interested.

Two upsert rules in `sync.py` keep the feed usable, and now a third:

- **A re-sync never clears `dismissed`.** Boards re-list the same posting every
  night. A feed that makes you reject the same job twice is one you stop opening.
- **A re-sync never touches `created_at`**, which is what "new since yesterday"
  measures from.
- **A re-sync only re-triages a posting whose description actually changed.**
  Boards re-list byte-identical text nightly; without this the model would spend
  every night reproducing yesterday's verdicts. But a genuinely rewritten
  posting _is_ re-judged, or a stale summary describes a job it no longer is.

**Adzuna's `description` is a truncated snippet, not the posting.** Its coverage
number is therefore computed against a summary and understates the match, so
those rows carry `partial: true` in `match_reasons` and the card marks the
number provisional. Triage fetches the real body before judging one, since
summarising a snippet produces a summary of a summary.

Board slugs are user input that lands in a URL path, so `sources/base.py`'s
`clean_slug` validates them to `[A-Za-z0-9_-]+`. That is the one
security-relevant line in `sources/` — unlike `ingest.py` the hosts are fixed
constants, so there is no `assert_public_url` to lean on.

## Resolving a company to a board

**Slugs cannot be guessed, which is why `resolve.py` exists.** Ada's Greenhouse
board is `ada18`; Cohere's Ashby board is `cohere`. A slug field asks the user
to go and find it, which is the tedious half of the work this feature removes.
So the input is a careers page URL, and detection is regex over the raw HTML —
which is why `ingest.fetch_html` was split out of `fetch_posting`: stripping to
text throws away the `href`s that hold the answer. The final URL after
redirects is scanned too, since a careers page that simply redirects to the
board carries the whole answer there and may have no useful body.

**A detected slug is not believed until the board API answers.** A regex can
match a URL that merely resembles a board, and an unverified guess becomes a
source that silently never syncs — the worst outcome, because it looks
configured. An empty board is still success: companies pause hiring.

Recognised-but-unsyncable ATSes (Workday, BambooHR, Workable, SmartRecruiters,
iCIMS…) are **named in the result** rather than reported as "nothing found".
"We found Workday and cannot read it" sends the user to the careers page;
"no board found" sends them looking for something that was never there.

## The resume queue

`queue.py` is a single-slot worker in the shape of `research/worker.py`,
deliberately not on `backend.ai.background`'s shared FIFO — twenty queued
resumes there would head-of-line block journal polish and every other
seconds-after-a-tap flow. It is the only part of the jobs scheduler that
touches the model, so it is the only part that defers through
`backend/ai/priority.py`.

**A failed application is skipped until it is re-queued.** Without that, one
posting the model chokes on sits at the head of the queue and is retried every
five minutes forever while nothing behind it is ever built. The failure is
written to `applications.queue_error`, shown in the UI, and cleared by
re-queueing — which is the explicit retry.

`build.py` holds the tailor-render-persist body that both the interactive route
and the worker call. It was extracted rather than copied because the two would
drift: one would keep rendering DOCX after the other stopped.

## Email linkage

The classifier that matters already existed: `backend/ai/email.py` tags mail
`category='job_application'` and sub-tags it `sent | rejection |
interview_next_step | other_update`. This module supplies what those tags
finally point at.

The hard part is that **the sender is usually not the employer** — Greenhouse
mail comes from `greenhouse.io`. `ATS_DOMAINS` names those senders so the
domain signal is skipped rather than spent, and the company name in the subject
carries the decision instead.

`best_match` then applies two rules the additive score cannot express, because
both are about the field of candidates rather than any one of them:

- **Uniqueness beats magnitude.** An ATS email naming exactly one of your
  employers identifies it, however little that scored.
- **A close runner-up blocks everything.** Two applications to the same company
  is where a confident guess quietly corrupts the record.

`job_email_scans` records "considered" so a large mailbox is walked once. That
verdict is only true relative to the applications that existed at the time, so
`rescan_since` clears the misses whenever an application is submitted — the
confirmation email usually arrives _before_ the user records that they applied.

## Applying: the browser extension

The last mile is a real ATS form in a logged-in session, which the backend
cannot reach. `extension/` is an unpacked MV3 extension that fills it — see
[extension/README.md](../../extension/README.md) for how it is put together.
Three things here exist for it:

**`GET /applications/for-url`** answers "which application is this tab?" using
`urlmatch.py`. That module is strict on purpose: exact-after-normalization or
same-host-same-path, and **ambiguity resolves to None**, never a best guess.
The extension records answers against whatever comes back, so a confident wrong
match files an interview answer under the wrong employer — the same reasoning
that makes `linkage.best_match` refuse a close runner-up. Tracking parameters
are stripped; `gh_jid` is emphatically _kept_, because on an embedded board it
is the only thing separating two postings.

**`application_answers`** is what was actually typed into one employer's form,
as distinct from `profile_answers`, which is the reusable bank the Answer Kit
draws on. A bank entry is a template; a row here is testimony. Recording
**upserts on the question text** rather than replacing the set — a Workday
application spans several pages, and replace-all would drop page one the moment
page two was recorded. It also makes the extension's record-as-you-fill safe to
repeat: correcting a field and re-recording updates the row instead of leaving
two contradictory answers to one question. Capture is as-you-fill rather than
on submit because SPA forms frequently never fire a real `submit`.

**`PATCH /resumes/<id>`** applies hand corrections and re-renders in place.
`tailor.apply_edits` is the merge, and the important thing about it is what it
_does not_ do: **it never clamps the user's wording against the profile.**
`clamp` exists to stop the model inventing experience; this is the person whose
name is on the document, and re-applying that bound would silently delete their
own edit. What is protected instead is structure — `bulletId`, `roleId`,
`company`, `roleTitle` and `original` all come from the stored row, so an edit
can reword an accomplishment but never re-attribute it to a company the user
never worked at, and the diff keeps a truthful "before". Editing **409s once
`applied_at` is set**, which is what keeps the version a record of what was
sent.

## Retention

Two clocks, whichever comes first: `applied_at + job_retention_days` (180), and
`closed_at + job_rejection_grace_days` (30) once rejected. `closed_at` exists
because `updated_at` would restart the clock every time a note was edited.

**Only the rendered files are deleted.** `resume_versions.content` and `html`
are kept forever — a few kilobytes each, and they are the answer to "what did I
actually send these people?", which is the question that gets asked a year
later, usually right before a recruiter calls back. `application_answers` is
the same category of evidence and is likewise never purged; `purge_application`
touches only `resume_versions` and `applications`, and a test pins that.

## Layout

| file               | role                                                                     |
| ------------------ | ------------------------------------------------------------------------ |
| `linkage.py`       | pure scoring + status advance. No DB, no network, no model               |
| `keywords.py`      | pure JD↔profile keyword gap — also the feed's match score                |
| `retention.py`     | pure date policy + the purge executor                                    |
| `profile.py`       | DB reads in the shapes tailoring and rendering want                      |
| `resume_import.py` | an existing `.docx`/text → the profile, bullets index-bound              |
| `tailor.py`        | the bounded-schema resume call                                           |
| `build.py`         | tailor → render → persist, shared by the route and the queue             |
| `answers.py`       | form filling: profile → bank → model, in that order                      |
| `render.py`        | one HTML template → preview, WeasyPrint PDF, python-docx                 |
| `ingest.py`        | one user-supplied URL → structured job                                   |
| `sources/`         | one adapter per board → normalized dicts. No DB, no model                |
| `resolve.py`       | careers page URL → which ATS + slug, verified against the API            |
| `sync.py`          | saved searches → `jobs` rows, scored inline                              |
| `triage.py`        | pure: the title gate, stated seniority, years demanded                   |
| `triager.py`       | applies triage to the DB: the free sweep + the model worker              |
| `queue.py`         | the single-slot resume worker behind the phone's Queue button            |
| `linker.py`        | applies `linkage.py` to the database                                     |
| `urlmatch.py`      | pure: is this browser tab that posting? Declines on ambiguity            |
| `scheduler.py`     | linkage + sync + gate + both drains every tick, purge daily 07:00–08:00  |
| `storage.py`       | `IdScopedStorage('JOBS_ROOT', './data/jobs')`                            |
| `backfill.py`      | confirmation mail → applications, for a search that predates the feature |

## Backfilling a search that started before the feature

`backfill.py` reconstructs `applications` from the confirmation mail they
produced, because the feature only records what you apply to _through_ it — a
mailbox with thousands of classified job emails and no applications gives
linkage nothing to attach to, which is exactly what the first real mailbox
looked like.

**It has no route, no button and no scheduler entry, so re-running it means a
shell.** The procedure, the reset SQL, and the numbers a healthy run produces
are in [docs/application-backfill.md](../../docs/application-backfill.md) —
including the ordering that matters (`backend/email/refetch.py` first, since
Indeed's plain-text part names no employer and only the HTML does).

Two rules decide what is created, and both are about refusing to store half a
fact rather than maximising rows:

- **A confirmation must name a company.** `geo.py`'s rule — an application with
  no employer cannot be linked or matched, so it pads the pipeline with entries
  that never resolve. The title may be empty; the company may not.
- **The whole normalized name must not be generic.** A live run created a
  company called "Software", and since linkage matches on company name it
  absorbed 144 links belonging to real employers. A poisoned row is worse than a
  missing one. The check is on the _whole_ name, not a substring, or every
  staffing firm in the mailbox disappears with it.

Rows are created at `submitted` and nothing here infers further: the linker
advances them from the very mail they were built out of. `commit()` reopens the
`job_email_scans` verdicts itself, from the earliest application rather than
from now — they were all reached against an empty applications table, and
`rescan_since`'s fortnight lookback around a current timestamp would leave years
of them standing.

## Things that will bite

- **Never hold a transaction across a model call.** `get_db()` is one
  process-global connection. Write, commit, _then_ call the model.
- **`recompute_purge_after` takes the status as an argument**, overriding the
  stored column, so a caller cannot get the date wrong by stamping before it
  writes.
- **Both renderers are imported lazily.** A missing WeasyPrint costs the PDF
  and nothing else; `is_pdf_available()` lets the UI say so.
- **`ingest.py` fetches a client-supplied URL from inside the network**, so
  `assert_public_url` on every redirect hop is load-bearing, not decorative.
- The linkage and sync sweeps make **no model calls**, which is why the
  scheduler runs both every five minutes without touching
  `backend/ai/priority.py`. Only the queue drain asks the gate.
- **Queued is a column, not a status.** `applications.status` has a baked-in
  `CHECK(...)` and SQLite cannot ALTER a constraint, so a tenth status would
  mean rebuilding the table. `queued_at` says the same thing for one line.
- **Every scheduler sweep is wrapped separately.** A board being down must not
  cost the linkage result computed earlier in the same tick.

## Not built (deliberately)

**No LinkedIn or Indeed scraping.** Cloudflare, no public API, and it is the
unambiguous part of their terms. Adzuna carries much of the same inventory
through a documented API, which is why it is the aggregator here. The extension
will happily _fill a form_ on whatever page you open, including those two —
that is your own logged-in session, doing what you would do by hand — but
nothing crawls them.

**Nothing auto-submits.** The extension fills; the human presses the button. An
agent that sends applications on its own is a different and much riskier
product, and the review step is the only thing standing between a model's
wording and an employer.

**No Firefox build**, though MV3 keeps it close. **No iOS**: Chrome on Android
and iOS have no extensions, which is exactly why the phone's job is triage and
the desktop's is applying, and why the Answer Kit stays.
