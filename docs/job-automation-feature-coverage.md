# Job automation `[fits]` coverage

This records the implementation produced from
`job-automation-feature-research.md`. Every item tagged `[fits]` is represented
below; existing Jobs behavior is called out where the research item was already
substantially present.

## Discovery and triage

- ATS API discovery: existing Greenhouse/Lever/Ashby adapters plus persisted
  Workday CXS boards (`jobs/sources/workday.py`, `jobs/workday_watch.py`).
- Career-page watch: scheduled, SSRF-guarded URL diffing in `career_watch.py`.
- Saved hunts: named searches now accept source-independent title, location,
  remote, salary-floor, and seniority filters and preserve new-since deltas.
- Extraction: arbitrary URLs use JSON-LD, then CSS candidates, then bounded AI.
- Posting text is explicitly untrusted in every JD-consuming model prompt.
- Numeric keyword coverage and model fit/rationale remain persisted and visible;
  hard deal-breakers now run before AI while soft preferences annotate results.
- Skill-frequency aggregation is exposed in Jobs analytics.

## Application documents and forms

- ATS review includes matched/missing keywords, action verbs, quantified impact,
  section sanity and parseability.
- Generated PDFs are checked through their actual text layer for contact details,
  reading order, and claimed keyword survival.
- Cover letters are generated only after the application is marked as requiring
  one.
- Resume generation now performs one schema-bounded reviewer revision. The
  reviewer can only select real bullet indexes and supported keywords, preserving
  the same anti-fabrication bounds as the drafter.
- The extension remains fill-without-submit, re-scans after answers reveal
  conditional fields, and persists both per-run state/screenshots and submitted
  answers. Prior submitted answers are now reusable FAQ memory across applications.

## Interview, outcomes and reporting

- Persisted interview packs use the exact archived posting, resume, cover letter,
  and notes; behavioural questions map only to real stored bullet/story IDs.
- Company/interviewer research stores source URLs beside supported claims and
  rejects unsourced prose.
- Stale applications, explicit ghosting, follow-up and interview thank-you drafts
  use archived submission facts.
- Status events provide dated stage history. Email linkage proposes cited changes
  and applies them only after confirmation.
- Dashboard analytics cover funnel, response rate/time, source conversion and
  weekly activity. Extension fill runs and screenshots are reviewable.
- Offline export is a self-contained HTML dashboard and application table.
- Upskill analysis weights missing skills across target postings and can attach
  verified learning-resource links.

## Profile defaults

- Stored screening defaults cover work authorization, salary, notice period,
  availability date, relocation, security clearance, and EEO answers.
- Company blocking supports an explicit blacklist and defaults derived from past
  employers.
