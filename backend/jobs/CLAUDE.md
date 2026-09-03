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

**The tap does not wait for that row either.** Both feed decisions go through
the app's offline write queue (`MUTATION_KEYS.jobDecide` in
`src/offline/mutationDefaults.ts`): the card leaves the cached feed in
`onMutate` and the POST follows, so triaging stays a rhythm rather than a
round trip apiece — and a decision made with the backend out of reach is
parked and replayed instead of lost, which on a bus is the normal case. Both
endpoints are idempotent for exactly that reason (re-queueing clears the
error and re-stamps `queued_at`; dismissing twice is one dismissal), because a
replayed decision must cost nothing. The feed reads what is still in flight
out of the mutation queue rather than component state, so a card stays gone
even when a refetch of `/feed` overtakes its write and hands the posting back.

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

### Distance, and why it is a gazetteer

`distance.py` answers the feed's other question — "could I actually get
there?" — and is built exactly like `keywords.py`: pure, deterministic,
computed at sync time, no network and no model. `distance_km` is cached on the
row beside `match_score` for the same reason that one is, so a 200-job sync is
still free and still sorted the moment it lands.

**An unrecognised place resolves to None, never to a large number.** That is
`geo.py`'s rule one layer up, and it is why provinces and countries are
_absent_ from the gazetteer rather than present with a centroid: "Remote -
Canada" has no point worth measuring, and a fabricated 4,000 km would sort a
real job off the end of a feed that orders on this column. A bare ambiguous
name resolves to None too — London ON and London UK are 5,500 km apart, so
`london` alone is declined the way `urlmatch.py` declines an ambiguous tab,
while `london on` and `london uk` are ordinary entries.

**Remote is not zero kilometres.** It is a different state, carried by
`jobs.remote`, and the distance sort gives it its own band ahead of the located
rows. Folding it in would rank every remote posting ahead of a job three subway
stops away.

Matching is longest-match-wins over token spans — `keywords.py`'s shape, for
`keywords.py`'s reason — and then **nearest wins** among what survives, so a
posting listing three offices is measured at the one you could reach. Adzuna is
the only adapter that carries real coordinates; they beat the gazetteer when
present, and `recompute_distances` digs them back out of stored `raw` so rows
synced before the column existed are fixed without re-fetching a board.

### What the model is allowed to add to it

The gazetteer reads `jobs.location`, which the boards fill in themselves. That
covers the ordinary case and needs no model — running one over a structured
field would swap a fact for an inference. The case it cannot cover is a body
that **contradicts** the field: "Remote - Canada" that turns out to want two
days a week in a Toronto office.

`ai/job_triage.py` already reads every posting body, and the prefill is the
expensive part, so that verdict carries two more fields for ~nothing:

- **`workLocation`** (`onsite | hybrid | remote | unclear`) lands in
  `jobs.work_location`, a **second column beside `jobs.remote`, never an
  overwrite**. The board's flag is the employer's own statement; this is a
  model reading prose. Keeping both is what makes the disagreement legible
  instead of destroying one of the two answers.
- **`cities`** is `enum`-bound to `distance.selectable_places()` — the
  gazetteer's own keys, minus the ambiguous bare names. So **the model cannot
  name a place, only point at one the gazetteer can already measure**. That
  keeps the module's rule intact: the model never produces the kilometres. An
  unbounded free-text city would put a fabricated location straight onto the
  sort key, which is the one thing this feature refuses to allow — the
  `missingMustHaves` and `tailor.py` trick, pointed at a third problem.

`_store_inferred_distance` then fills `distance_km` **only where it is still
NULL**, guarded in SQL rather than in Python so a re-triage racing a re-sync
cannot win. The complement of that guard lives in `sync.recompute_distances`,
which must **not clear an `inferred` reading**: it recomputes from the same
`location` field the model was called in for, so it produces None for exactly
those rows — and since the writer only fills a NULL, an unguarded overwrite
could only be undone by a re-triage. It also has to look inside Workday's
`raw = {'listing', 'detail'}` wrapper for coordinates rather than at the top
level alone. Precision is `'inferred'`, the weakest of the four and the only one
that did not come from a structured field. A row banded as remote by the sort
is one where `work_location` did _not_ contradict the flag, so the hybrid case
sorts at its real distance instead of hiding at the top.

### The commute radius

`preferences.hard_gate` decides location in exactly one place: a
`maxDistanceKm` on the profile, rejecting a posting only when
`distance.verdict` returns **`out_of_range`**. The verdict is three-valued and
that is the whole design — `unknown` fails open, because a location the
gazetteer could not read is missing information about a posting, not a verdict
on it. Fully remote is exempt, read from the structured `remote` +
`work_location` pair the distance band uses rather than searching prose.

**It replaced `remoteOnly` and `allowedLocations`, which are gone from the
gate and the profile UI.** Both decided geography with string operations — one
searching the body for "remote"/"on site" phrases, the other substring-matching
a comma-separated list against the location string — so neither could tell that
"Mississauga" is 24 km out and "Bengaluru, India" is not. Three overlapping
location gates is also how a posting gets rejected for a reason the user cannot
predict. **The two columns are kept** (`job_profile.allowed_locations`,
`job_profile.remote_only`), unread: this codebase's migrations are additive and
nothing is destroyed, but a value left in either must no longer filter, or a
stale setting would keep silently hiding postings. A test pins that.

Not to be confused with the **per-search** `remoteOnly`, which is alive and
unrelated: it is an Adzuna query parameter in `job_searches.params`, read by
`sync.matches_hunt` and `sources/adzuna.py`, and scopes one saved search rather
than gating every posting.

Two things follow from putting it here rather than in the feed's `WHERE`. The
rejection is a **state with a reason**, so it lands in the existing
`Filtered out (N)` section with a Restore button and no new API surface. And
`preference_keys` already resets every cached verdict when a preference
changes, so raising the radius re-reveals rows with no bespoke invalidation.

**A region can be out of range without having a distance.** `_FAR_REGIONS`
names countries, provinces and US states with no point within 200 km of the
anchor — a _bound_, not a centroid, which is why it coexists with the rule that
countries have no point worth measuring. It never produces a `Reading` and
never reaches `distance_km`. `ontario`, `canada`, `new york` and
`pennsylvania` are deliberately absent: each contains points that _are_ in
range (Buffalo and Niagara Falls NY are ~130 km out), so the region says
nothing. Measured on the live feed it settles 425 of the 744 rows the
gazetteer had left unplaced.

`tokenize` also folds diacritics now. `[^a-z0-9]+` treated an accent as a
separator, so `Montréal` split into `montr` + `al` and never matched — as did
`São Paulo`, `Bogotá` and `Québec`. `paris` joined `london` and `cambridge` in
`_AMBIGUOUS` for the same reason those are there: Paris, Ontario is ~95 km out
and would be _in_ range.

### The scope a registered careers URL already carries

Almost every URL in [docs/toronto-tech-companies.md](../../docs/toronto-tech-companies.md)
was collected from a company's **Toronto** careers page, and about a third say
so in the query string — `?offices[]=87006` is Stripe's Toronto office,
`?locationCountry=a30a87ed…` is Workday's Canada facet. Registration reduced
each to a bare slug, so a board the user had scoped to one city was synced
worldwide. On the ten scoped Greenhouse boards that was 365 of 452 rows.

`resolve.scope_filters` reads the scope out of the URL at registration;
`workday.parse_board_url` returns its facets alongside host/tenant/site. Then:

- **greenhouse** filters client-side on `offices[].id` **and `child_ids`** —
  the offices are a tree and matching only the exact id drops postings filed
  against a child. Filtering on the _office_ rather than the location text is
  the point: it correctly keeps the Toronto postings whose location reads
  `N/A`, `TOR` or `CA-Toronto, CA-Montreal, CA-Vancouver`, none of which any
  gazetteer places.
- **lever** filters on `categories.allLocations`, not the singular
  `categories.location` — a role open in several cities lists them all there.
- **workday** sends `appliedFacets`, so the filter is applied by Workday. That
  also matters against `MAX_POSTINGS = 200`: an unfiltered global board could
  exhaust its budget before reaching a Canadian row. Facet parameter _names_
  are forwarded as written (`locations`, `locationCountry`, `Location_Country`
  and `LocationCountry` are four spellings across the registered boards), but
  only names containing "location" or "country" — forwarding everything would
  hand Workday a tracking blob as a facet, and an unrecognised facet returns an
  empty board.
- **ashby is deliberately unscoped.** Its boards filter with
  `?locationId=<uuid>` and the posting API returns no location id, so the
  filter cannot be honoured. Storing it would look configured and filter
  nothing.

Backfilling the two tables differs because only one of them kept the URL.
`workday_boards.url` is on the row, so `_ensure_workday_board_facets`
re-derives the facets at startup with no network. `job_searches` stores no URL,
so the doc is the only place that scope still exists — hence
`scripts/import-company-boards.py --refresh-filters`.

The sort is `GET /feed?sort=distance`, and it replaces only the **inner** key:
the fit bucket stays primary because it is what the client groups cards by, and
a mode that reshuffled the groups would be a different feed rather than the
same one reordered. Unplaced rows fall back to the keyword score rather than
sinking without one — a location the gazetteer could not read is missing
information about a posting, not a verdict on it, and most rows in a mailbox
backfill have no location at all.

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

The **triage** drain works the same way with one difference that matters at
scale: `triager.drain_while_idle` keeps judging for up to
`DRAIN_BUDGET_SECONDS` instead of doing one and sleeping. `drain_once` submits
exactly one and returns — correct for a tailoring pass, which is minutes long
and queued a handful of times a day — but under the 300-second tick it gave
triage a ceiling of **twelve postings an hour**: four seconds of model, 296 of
sleeping. Invisible while a few boards produced tens of postings a day; a
month-long queue the moment the backlog was thousands.

The deferral behaviour is unchanged and is simply re-read every iteration
rather than every five minutes, so a chat message still stands the drain down
between generations. Two things keep the loop honest: each submission is
**waited on** before the next is considered (otherwise the executor just
accepts a queue and no gate is read between them), and the loop **stops when
the next candidate is one it already judged**. That second guard is not
theoretical — `process_one` deliberately leaves a row `pending` when the model
is unreachable, because a verdict nobody reached must not be recorded, and
without it an unreachable llama-server would mean thousands of retries inside
one tick instead of one every five minutes. It looks ahead rather than checking
after the fact, so the repeat is never handed to the worker.

The queue drain runs **before** the triage drain, since the triage drain can
hold the tick for minutes and a resume was explicitly asked for by tapping
Queue while triage is speculative work over postings nobody has opened.

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

## Pausing the whole thing

`settings.jobs_paused`, read by `scheduler.is_paused()` on every tick and
toggled from the Sources panel above the feed. It stops board sync, the career
and Workday watches, the triage gate, the triage drain and the resume queue —
everything that reaches a third party or spends a llama slot.

**Linkage, ghosting and retention keep running through a pause**, because none
of them does either, and stopping them would quietly rot the pipeline while the
user believed they had paused only fetching: a rejection that lands during a
fortnight's pause should still be on the application when it ends.

**It is a flag, not `UPDATE ... SET enabled=0` across the three source
tables.** A pause has to be lossless — mass-toggling rows forgets which sources
the user had switched off by hand, and Resume would turn those back on. Nothing
per-source is written either way, so unpausing restores exactly the
configuration that was there. Reading the flag is wrapped: a database that has
not run the migration yet behaves exactly as it did before the switch existed.

The `Paused` badge shows on the _collapsed_ panel header. A pause nobody can
see is a pause that gets forgotten, and then looks like a feed that broke.

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
| `distance.py`      | pure: km from the commute anchor. Declines rather than guesses           |
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
| —                  | …and `is_paused()`, the one switch that stops all of that reaching out   |
| `storage.py`       | `IdScopedStorage('JOBS_ROOT', './data/jobs')`                            |
| `backfill.py`      | confirmation mail → applications, for a search that predates the feature |

`scripts/import-company-boards.py` is beside it in spirit: a Markdown company
list (`docs/toronto-tech-companies.md`) → `job_searches` and `workday_boards`
rows, reusing `resolve.find_candidates` rather than growing a second copy of
its regexes. It does **not** verify each slug against the live board the way
`resolve.py` does — that is one network call per company, and 220 of them is a
rate-limit incident. The first scheduled sweep verifies them the ordinary way,
and a bad slug shows red in the sources panel. Dry run by default; idempotent
on `(kind, slug)` so re-reviewing the list does not double every source.

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
