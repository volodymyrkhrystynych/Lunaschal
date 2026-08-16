# Job applications

Profile → tailored resume → application → email trail → deletion. The Jobs tab
(`src/components/Jobs/`) is the UI; `backend/routes/jobs.py` is thin and every
judgement call lives in a pure module here.

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

## Retention

Two clocks, whichever comes first: `applied_at + job_retention_days` (180), and
`closed_at + job_rejection_grace_days` (30) once rejected. `closed_at` exists
because `updated_at` would restart the clock every time a note was edited.

**Only the rendered files are deleted.** `resume_versions.content` and `html`
are kept forever — a few kilobytes each, and they are the answer to "what did I
actually send these people?", which is the question that gets asked a year
later, usually right before a recruiter calls back.

## Layout

| file           | role                                                       |
| -------------- | ---------------------------------------------------------- |
| `linkage.py`   | pure scoring + status advance. No DB, no network, no model |
| `keywords.py`  | pure JD↔profile keyword gap                                |
| `retention.py` | pure date policy + the purge executor                      |
| `profile.py`   | DB reads in the shapes tailoring and rendering want        |
| `tailor.py`    | the bounded-schema resume call                             |
| `answers.py`   | form filling: profile → bank → model, in that order        |
| `render.py`    | one HTML template → preview, WeasyPrint PDF, python-docx   |
| `ingest.py`    | one user-supplied URL → structured job                     |
| `linker.py`    | applies `linkage.py` to the database                       |
| `scheduler.py` | linkage every tick, purge daily in 07:00–08:00             |
| `storage.py`   | `IdScopedStorage('JOBS_ROOT', './data/jobs')`              |

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
- The linkage sweep makes **no model calls**, which is why the scheduler can
  run it every five minutes without touching `backend/ai/priority.py`.

## Not built (deliberately)

Discovery (Adzuna + Greenhouse/Lever/Ashby adapters, match scoring) and the
reverse-proxy browser tab with an injected fill button are phases 2 and 3. The
Answer Kit — paste the questions, get tap-to-copy answers — is the path that
works on every device including iOS, and it ships first for that reason.
