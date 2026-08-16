# Jobs tab — design doc

**Status: phase 1 built and tested, not yet run against a live job search.** The profile, resume
tailoring, application tracking, the Answer Kit, email linkage and resume retention all ship in
`c30e811` on `feat/job-applications`, with 151 backend and 20 frontend tests. Discovery (phase 2)
and the in-app browser (phase 3) are designed but not built — see
[The three phases](#the-three-phases). Nothing here has been exercised against a real posting, a
real ATS, or a real rejection email yet; [What we do not know yet](#what-we-do-not-know-yet) is
honest about which parts that makes provisional.

This is the design record. `backend/jobs/CLAUDE.md` documents how the code works and what will
bite someone changing it; this documents why it is shaped this way, and what was rejected.

## Why the tab exists

Applying for jobs is three separate record-keeping problems that all decay if kept by hand:

1. **What you know.** A resume is a lossy snapshot of it, and the snapshot goes stale.
2. **What you sent, to whom.** Six months later a recruiter calls back and the question is "which
   version of my resume does this person have in front of them?"
3. **What came back.** Replies arrive in email, which is where they stay.

The third one was already half-solved and nobody had noticed. `backend/ai/email.py` has been
classifying synced mail as `category='job_application'` and sub-classifying it
`sent | rejection | interview_next_step | other_update` for a while, and the Email tab's
`JobDashboard` counted those. But counting emails is not counting applications — three rejection
emails about one job read as three rejections — because there was no application entity for the
tags to point at. Phase 1 is largely the work of creating that entity and wiring the existing
classifier to it.

## What the build settled

- **Bullets are rows, not prose.** This is the load-bearing decision. Storing the profile as one
  document would have been simpler for editing and would have made the anti-fabrication guarantee
  impossible, because you cannot bound a model to a list that has no indexes.
- **The keyword list is computed, not asked for.** Asking a model "what keywords should I add?"
  gets you keywords. Some of them will be things you cannot do.
- **Two vocabularies for status, meeting in exactly one place.** An email's `job_status` and an
  application's `status` are different questions ("what is this message?" vs "where has this got
  to?"). `linkage.EMAIL_STATUS_MAP` is the only crossing point.
- **The Answer Kit ships before the browser overlay.** Reversing that order would have put the
  most fragile component in front of the useful one.
- **Retention deletes files, not records.** These turned out to be entirely separable, and the
  reason to delete is entirely about the former.

## The anti-fabrication problem

This is the part of the feature that could do real damage, so it got the most design.

An LLM asked to "tailor this resume to this posting" will, reliably and without being asked,
improve your career. It adds a technology the posting mentions. It promotes "contributed to" into
"led". It invents a percentage. Every one of those is a thing you will be asked about in an
interview by someone holding the document.

Prompt instructions do not fix this. "Do not invent experience" is a request, and the failure mode
is silent — the output looks exactly like the good output.

Two structural mechanisms instead, neither of them a prompt:

**Selection by bounded index.** The model never writes a bullet from scratch. It receives a
numbered list of the user's real accomplishments and returns `{"index": 3, "rewritten": "..."}`.
The JSON Schema declares `index` as `{minimum: 0, maximum: n-1}`, and llama-server compiles that
schema into a GBNF grammar before decoding. An index of 47 against a 12-bullet profile is not
rejected after the fact — it is not a token the sampler can emit. This is the same trick
`backend/ai/idea_assessment.py` uses to stop the Ideas agent citing files that do not exist, and it
is the reason `profile_bullets` is a table.

**Keywords as an enum of what is provable.** `keywords.py` computes, deterministically, which of
the posting's terms the profile can evidence — searching skills, bullets, summary and role titles,
so a bullet describing a Kubernetes migration counts as Kubernetes whether or not it was typed into
the skills list. Only the supported terms are offered to the model, as a schema `enum` on the
`emphasis` field. The unsupported ones go into the prompt explicitly labelled as forbidden. So the
ATS-keyword-matching the feature exists to do happens **only** in the direction where it is true,
and the gaps are shown to the user as gaps rather than quietly filled in.

### What this does not fix

Inflation _inside_ a rewrite. "Helped with the billing migration" → "Led the billing migration"
selects a real bullet and stays within the schema. No bound catches it, because it is a claim about
degree, not existence.

So the design stops pretending and shows the work: `clamp()` stores `original` alongside every
`text`, `rewritten` marks which ones changed, and `ApplicationDetail` renders a strikethrough diff
of every reworded bullet before you can download anything. The last check on a resume is the person
whose name is on it. The system's job is to make sure they can see what changed.

## Email linkage

### The sender is not the employer

The obvious signal — does the sender's domain match the job posting's domain? — is close to
useless in practice, because most application mail comes from an applicant-tracking system.
`no-reply@greenhouse.io` scores identically against every Greenhouse application you have open.

`ATS_DOMAINS` lists the senders where this is true (Greenhouse, Lever, Ashby, Workday, iCIMS,
SuccessFactors, Taleo, SmartRecruiters, Workable, BambooHR, Jobvite, Breezy, Recruitee, Teamtailor,
Rippling). For those, the domain signal is **skipped rather than spent**, and the decision falls
through to the company name in the subject or body, and to how much of the job title survives into
the subject line.

Title matching is fractional, not exact: "Senior Backend Engineer, Payments" arrives as "Your
application to Acme — Backend Engineer", so `title_overlap` measures what fraction of the title's
significant words appear, with generic words (`remote`, `position`, `full`, `time`…) dropped
because they appear in every posting and every subject line and would inflate every score equally.

### Two rules the score cannot express

Scoring each candidate independently gets the ATS case wrong in an instructive way. A Greenhouse
rejection saying "Your application to Acme" earns no domain credit by design, so it tops out around
0.35 — well under the 0.6 auto-link threshold — even when Acme is the only employer you have
applied to. That is obviously identifiable, and the additive score cannot see it, because
uniqueness is a property of the _field_ of candidates.

So `best_match` applies two rules after ranking:

- **Uniqueness beats magnitude.** If exactly one candidate matched on company name, that
  identification is unambiguous however little it scored.
- **A close runner-up blocks everything.** If the top two are within 0.15, nothing auto-links.
  Two applications to the same company is precisely where a confident guess quietly corrupts the
  record, and where asking costs one tap.

This was found by a failing test, not by design. The first implementation was pure additive
scoring; the test asserting that a Greenhouse rejection finds its application failed, and the fix
was to move the judgement somewhere that could see all the candidates at once.

### Status can only move forward

`advance_status` is monotonic along `PROGRESS_RANK`. A confirmation email that syncs late cannot
walk an application back from `interview` to `acknowledged`; nothing reopens a `rejected` without a
human; a rejection arriving after an offer is ignored as far likelier to be stale than real; and
`withdrawn` is never overwritten, because an automated status change must not overrule someone who
explicitly walked away.

### The rescan problem

`job_email_scans` records that an email was considered, so a two-thousand-message mailbox is walked
once rather than on every five-minute tick. But "no match" is only true relative to the
applications that existed at the time — and the confirmation email usually arrives _before_ the
user gets round to recording that they applied. Left alone, the mail most likely to be an
application's confirmation is the mail permanently marked unmatched.

`rescan_since` clears the misses whenever an application is submitted. Confirmed matches survive.

## Retention

Two clocks, whichever fires first:

- `applied_at + job_retention_days` (default 180)
- `closed_at + job_rejection_grace_days` (default 30), once the application is rejected, withdrawn
  or ghosted

`closed_at` exists as its own column because `updated_at` cannot serve: editing a note eight months
later would push it forward and silently grant another six months of storage. An offer never takes
the short clock — that is the one outcome where the paperwork matters most.

**Only the rendered files are deleted.** `resume_versions.content` (the structured tailoring
result) and `html` are kept forever. They are a few kilobytes, and they answer "what exactly did I
send these people?", which is the question that actually gets asked a year later — usually right
before a recruiter calls back. The reason to delete was never that the information is unwanted; it
is that a hundred stale PDFs on disk are clutter. So the clutter goes and the answer stays.
`download.pdf` on a purged version returns **410 Gone**, not a bare 404, because "this was deleted
on purpose" and "this never existed" should not look the same.

## The Answer Kit, and why it is not a browser

The original ask was a built-in browser with a button floating over the live application page that
fills the form. That is the right end state and it is the fragile end state. The options, honestly
assessed:

| approach                                                             | verdict                                                                                                                                                                                                                          |
| -------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `<iframe>` the job site                                              | Dead on arrival. `X-Frame-Options: DENY` on every major board.                                                                                                                                                                   |
| Reverse-proxy through Flask, strip frame headers, inject the overlay | Workable on ATS portals (Greenhouse, Lever, Workable, Ashby, Workday) — simple server-rendered forms, permissive CSP. Unreliable on logged-in LinkedIn/Indeed: strict CSP, `SameSite` cookies, service workers, fraud detection. |
| Browser extension                                                    | Best desktop UX, no proxy fragility. Impossible on iOS Safari.                                                                                                                                                                   |
| Bookmarklet                                                          | Blocked by CSP on exactly the strict-CSP sites that need it most.                                                                                                                                                                |
| Server-side Playwright, streamed                                     | Heavy — competes with the 35B model for the machine — and driving a remote browser from a phone is miserable.                                                                                                                    |

The proxy's weakness turns out to sit exactly where it does not matter: LinkedIn and Indeed are the
**Easy Apply** flows, which are already prefilled from your LinkedIn profile and need no help. The
forms that need help are the bespoke ATS portals, which are the ones a proxy handles well.

But that is still a component that a site redesign can break, so it is phase 3. Phase 1 ships the
thing that cannot break: **paste the form's questions, get a stack of tap-to-copy answers**, plus
the tailored resume ready to attach. On a phone that is tap, switch app, paste, switch back — more
tedious per field than autofill, and it works on every device including iOS, forever.

### Resolution order

The model is the last resort, not the first:

1. **The profile** — name, email, phone, location, links. These are facts. Asking a language model
   to reproduce a phone number is a way to get a phone number that is nearly right.
2. **The answer bank** — the standard questions every portal asks (work authorization, notice
   period, salary), matched by token overlap rather than string distance, because "Are you legally
   authorized to work in Canada?" and "Work authorization" share few characters and most of their
   meaning.
3. **The model**, for the rest.

A form of contact fields and standard questions is answered instantly, with no model call at all.
That is what makes the second tap free.

### Per-question schema bounds

The model call is shaped as **one object property per question** (`q0`, `q1`, …) rather than an
array of answers, so every question can carry its own schema: a dropdown becomes an `enum` of its
real options, a numeric field becomes an `integer`. An array cannot express per-item constraints.
The effect is that an unselectable answer to a dropdown is undecodable rather than merely unlikely.

## Two-stage steering

The requested interaction, and it maps cleanly onto the existing recorder:

- **Tap the mic** → dictate how you want this one handled ("emphasise the payments work, keep it
  short"). Stored on `applications.steer`, then run.
- **Tap the action button** → run with whatever steer is already stored, which after the first time
  is usually right and costs nothing.

Dictation reuses `useRecorder` (getUserMedia → MediaRecorder → `POST /api/transcribe`), the same
hook ten other views use, so it works over Tailscale HTTPS on a phone with no new plumbing. The
steer is placed last in the prompt and labelled as the user's own words, so it outranks the generic
guidance — but it is explicitly subordinated to the anti-fabrication rules, so "make me sound more
senior" cannot unlock a claim the bullets do not support.

## Mobile

No user-agent sniffing and none added; the app detects mobile with a media query
(`src/lib/breakpoints.ts`) and nothing else. The Jobs view uses `useMasterDetail` +
`MasterDetailBack`, the same list/detail collapse every other two-pane view uses, and 44px minimum
touch targets throughout.

**On "would I need to force desktop view on the phone?"** — for anything the _server_ fetches (job
ingest, and the phase-3 proxy) we set the User-Agent ourselves and always send a desktop string, so
the mobile-app interstitial never appears. `ingest.py` does this today. The only place the "open in
the app" nag can appear is your own mobile browser during an Easy Apply, where mobile web works
fine anyway.

## Scheduling

`jobs/scheduler` is the first daemon loop in the app that needs **no llama slot at all** — the
linkage sweep is pure string matching. So it does not go through `backend/ai/priority.py` and does
not need a window to itself; it sweeps every tick (300 s), which means a rejection that lands at
09:00 shows on the application by 09:05 rather than tomorrow.

Only the daily file purge keeps to a window: **07:00–08:00**, after the four model-using loops
(chat titling 02:00–03:00, repo context 03:00–05:00, briefing 05:00–07:00, research deferring
continuously). It costs nothing to stay out of their way and it keeps the schedule readable.

## Data model

Eleven tables. ULID primary keys, unix-int timestamps, the house conventions.

| table                                  | note                                                                                                                                            |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `job_profile`                          | singleton, seeded by the migration so no caller handles its absence                                                                             |
| `profile_roles` / `profile_bullets`    | bullets are the addressable evidence units                                                                                                      |
| `profile_skills` / `profile_education` |                                                                                                                                                 |
| `profile_answers`                      | the reusable answer bank; `slug` names the standard questions                                                                                   |
| `jobs`                                 | `UNIQUE(source, source_id)`; a hand-added job uses its own ULID as `source_id` so the constraint stays total                                    |
| `applications`                         | `UNIQUE(job_id)` — reapplying later is a new posting, which is also the honest model of it                                                      |
| `resume_versions`                      | `content` kept forever, `pdf_path`/`docx_path` nulled on purge                                                                                  |
| `job_email_links`                      | `UNIQUE(application_id, email_id)`                                                                                                              |
| `job_email_scans`                      | linkage bookkeeping lives here rather than as a column on `emails`, so this module owns all its own state and the email feature stays untouched |

`applications.status` is a nine-value enum; `jobs.match_score` is nullable and NULL means "not
scored yet", which is not the same as zero.

## Resume rendering

One HTML template is the single source of layout: the app previews that exact string and WeasyPrint
prints it, so what you approve on screen is what the employer opens. DOCX is built separately from
the same content dict — a Word file is a different object model, not a rendering of HTML — so the
two can differ in typography but not in content. DOCX is worth having because several ATS parse it
more reliably than PDF and some portals only accept it.

The layout is deliberately plain: one column, no tables, no text boxes, no sidebars, standard
section headings. Résumé screeners flatten documents to read them, and a two-column layout flattens
into interleaved nonsense. The prettiest resume that parses wrong scores zero.

A role with no selected bullets still renders its heading — dropping a job creates an unexplained
employment gap, which is worse than a heading with nothing under it.

Both renderers are imported lazily. A missing WeasyPrint costs the PDF and nothing else;
`is_pdf_available()` lets the UI say so plainly rather than failing the whole tailor.

## The three phases

**Phase 1 — the spine (built).** Profile, tailoring, applications, Answer Kit, email linkage,
retention. Chosen first because none of it can break from a third-party redesign, and everything
else bolts onto it.

**Phase 2 — discovery.** `backend/jobs/sources/` with one adapter per source behind a common
`fetch(search) -> list[JobPosting]`: Adzuna (free tier, Canadian coverage, aggregates broadly) plus
Greenhouse / Lever / Ashby company boards (public JSON, no auth, exact data). A `job_searches` table
drives a nightly sync, then LLM match-scoring against the profile — deferred through
`backend/ai/priority.py` like `research_scheduler`, so it never competes for the two llama slots.

Deliberately **not** attempted: bulk-scraping Indeed and LinkedIn. Cloudflare, no public API, no
headless browser in this stack, and against both sites' terms. Adzuna covers much of the same
inventory legally. The `jobs.source` enum has no `linkedin` or `indeed` value for that reason.

**Phase 3 — the proxy tab.** `backend/routes/browse.py`: same-origin reverse proxy, per-domain
cookie jar reusing the `site_cookies` shape the fanfic module already uses, desktop UA,
`X-Frame-Options`/`frame-ancestors` stripped, URL rewriting, app-banner and `linkedin://` deep-link
removal, and an injected overlay with the 🎤 steer and ⚡ fill buttons posting detected fields to
the existing `/api/jobs/applications/<id>/answers`. Scoped to an ATS-domain allowlist at first.
`assert_public_url` on every hop is mandatory, not optional — it is a user-driven fetcher pointed at
arbitrary URLs.

## What we do not know yet

Phase 1 is tested but has never met a real job search. The parts that are genuinely provisional:

- **Every linkage threshold is a guess.** `AUTO_LINK_THRESHOLD = 0.6`, `SUGGEST_THRESHOLD = 0.3`,
  the 0.15 contest margin, the individual signal weights. They are calibrated against fixtures that
  were written from the same intuition that produced the weights, which is not evidence. A month of
  real mail will say whether it over- or under-links; the Inbox tab exists partly to make the
  failures visible.
- **`ATS_DOMAINS` is a list, and lists rot.** A sender not on it gets treated as an employer domain,
  which mostly means a missed link rather than a wrong one — the safer failure — but it will need
  additions.
- **`MAX_LOOKBACK_DAYS = 400`** assumes nobody replies after thirteen months. Probably true.
- **`BASE_TERMS` is one person's idea of what a posting might ask for.** It grows with the profile's
  own skills, which is the escape hatch, but the base list reflects a software-shaped job search.
- **Nobody has read a generated resume yet.** Whether the local Qwen3.6 produces rewrites worth
  keeping — or whether it mostly returns bullets unchanged, or mostly inflates them — is unknown.
  The diff UI exists so that this is discoverable rather than surprising.
- **The 60-word summary cap** is arbitrary.
- **`resume_versions` accumulates.** Re-tailoring makes a new row each time and nothing prunes the
  superseded ones before the retention date.
- **No cover-letter generation.** The column exists and is hand-edited.
- **No reordering in the profile editor.** `ord` exists on every child table; nothing sets it but
  append order.

## Build order

1. **Schema, migrations, pure modules** — `linkage`, `keywords`, `retention`, `storage`
2. **Model-facing modules** — `profile`, `tailor`, `answers`, `ingest`, `render`
3. **DB and HTTP layers** — `linker`, `scheduler`, `routes/jobs.py`
4. **Frontend** — `api.jobs`, the four view-registration edits, `src/components/Jobs/`
5. **`JobDashboard` rewritten** to count applications rather than emails

All of it in one commit, `c30e811`. The pure modules were written and tested before anything that
depended on them, which is how the two design corrections above (the uniqueness rule, and
`recompute_purge_after` taking its status as an argument) surfaced as failing tests rather than as
wrong data six months in.
