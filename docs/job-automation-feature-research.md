# Job-application automation — feature research

A survey of open-source GitHub projects in the job-application-automation space,
compiled into a menu of features Lunaschal's Jobs module could adopt. Research
done 2026-08-27.

The goal is **not** to build an auto-apply bot. Most of the projects below are
LinkedIn/Indeed Easy-Apply spammers; the interesting parts are the pieces around
that — scoring, tailoring bounds, interview prep, follow-up tracking, analytics —
which fit Lunaschal's existing "phone decides / desktop sends" split and its
local-only, single-user, no-cloud-API constraints.

Each feature is tagged:

- **[have]** — already in `backend/jobs/` (see [jobs-tab.md](jobs-tab.md))
- **[fits]** — not built, consistent with the current design and constraints
- **[maybe]** — plausible but needs a decision (scope, privacy, or effort)
- **[no]** — conflicts with a Lunaschal constraint (cloud API, mass automation,
  detection evasion, multi-user)

---

## Projects surveyed

| Project                                                                                                                | What it is                                                                                                                          | Notable ideas                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ---------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot)                                                               | 6-stage autonomous pipeline (discover → enrich → score → tailor → cover letter → auto-apply) driven by Claude Code + Playwright MCP | JSON-LD/CSS/AI cascade for job-description extraction; `resume_facts` preserved during tailoring ("never fabricates"); Workday portal adapters; dry-run mode; URL dedup                                                                                                                                                                                                                                                                                                                                      |
| [LinkedIn-AI-Job-Applier-Ultimate](https://github.com/beatwad/LinkedIn-AI-Job-Applier-Ultimate)                        | LinkedIn+Indeed bot, all job types not just Easy Apply                                                                              | **PII anonymisation before sending to LLM**, then de-anonymise; conditional/checkbox question handling; local web dashboard (run history, screenshot review, config editing, JSON export); in-demand-skill frequency stats; inbox triage (classify convos, draft replies, star recruiters); interest threshold vs "monkey mode"                                                                                                                                                                              |
| [LinkedIn_AIHawk](https://github.com/jomacs/linkedIn_auto_jobs_applier_with_AI) (`linkedIn_auto_jobs_applier_with_AI`) | The original mass LinkedIn applier                                                                                                  | `plain_text_resume.yaml` structured profile (auth status, salary expectation, self-ID, availability, work preferences); company/title blacklists; dynamic per-question GPT answerer tuned to company culture                                                                                                                                                                                                                                                                                                 |
| [EasyApplyJobsBot](https://github.com/wodsuz/EasyApplyJobsBot)                                                         | Multi-board Easy-Apply bot                                                                                                          | AI resume-enhancement suggestions (skills/experience/summary); board coverage list                                                                                                                                                                                                                                                                                                                                                                                                                           |
| [jobsync](https://github.com/Gsync/jobsync)                                                                            | Self-hosted tracker + AI career assistant (Next.js)                                                                                 | In-app AI chat: resume review with score + written feedback, job-match scoring, cover-letter gen; Greenhouse/Lever API discovery + AI-match; time-tracking linked to tasks; MCP server so Claude Desktop can add jobs / question-bank entries                                                                                                                                                                                                                                                                |
| [ai-job-search](https://github.com/MadsLorentzen/ai-job-search) (MadsLorentzen)                                        | Claude-Code-native framework, local, file-based                                                                                     | **Drafter → reviewer two-agent pipeline** (second agent researches company, critiques draft, drafter revises); ATS text-layer extraction to verify reading order + keyword coverage in the rendered PDF; `/interview` prep pack built from the exact posting + CV the interviewer read; question→STAR mapping; mock interview; `/upskill` skill-gap heatmap with time estimates; `/outcome` surfaces applications quiet 10+ days and drafts follow-ups; thank-you notes; postings treated as untrusted input |
| [job-application-bot-by-ollama-ai](https://github.com/lookr-fyi/job-application-bot-by-ollama-ai)                      | "End-to-end job agent", local Ollama                                                                                                | Semantic filters; ATS-optimised resume per job; **referrals from hiring managers**; company career-page search; reasoning-trace explanations; weekly activity summary; blacklist defaults to past employers; skip optional questions                                                                                                                                                                                                                                                                         |
| [jobgpt-mcp-server](https://mcp.so/servers/jobgpt-mcp-server)                                                          | 34-tool MCP server for job search                                                                                                   | Rich saved-search filters (salary, remote, H1B, company size, industry); "new matches from a saved hunt"; resume↔job match score tool; find recruiters / potential referrers                                                                                                                                                                                                                                                                                                                                 |
| [ats-checker](https://github.com/hugounoclaw/ats-checker)                                                              | Client-side 0–100 ATS score                                                                                                         | Score = parse-ability + keyword overlap (TF-style) + action-verb density + measurable-impact density + section sanity; prioritised fixes; 100% local                                                                                                                                                                                                                                                                                                                                                         |
| [ats-screener](https://github.com/sunnypatell/ats-screener)                                                            | Simulates 6 real ATS platforms (Workday, Taleo, iCIMS, Greenhouse, Lever, SuccessFactors)                                           | Per-platform parse/filter/score modelling                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| [Resume Matcher](https://resumematcher.fyi/)                                                                           | Open-source ATS resume scanner                                                                                                      | Keyword extraction from JD, match score, embeddings-based similarity, guided improvement                                                                                                                                                                                                                                                                                                                                                                                                                     |
| [ChatGPT/Ancastal cover-letter generators](https://github.com/JensBender/chatgpt-cover-letter-generator)               | Scrape posting URL → tailored cover letter                                                                                          | Straightforward URL → letter; combine posting with user background                                                                                                                                                                                                                                                                                                                                                                                                                                           |

---

## Feature menu

### 1. Discovery & sourcing

- **[have]** Multi-board discovery adapters (`backend/jobs/sources/`) → triage feed.
- **[fits]** Greenhouse / Lever / Workday **ATS-API discovery** — these expose JSON
  endpoints per company, no scraping. jobsync and ApplyPilot both lean on them.
  A curated list of "companies I'd work for" whose boards get polled directly.
- **[fits]** **Company career-page watch** — register a careers URL, diff it on a
  schedule, surface new postings. Complements board adapters for companies not on
  any aggregator.
- **[fits]** **Saved searches / "job hunts"** — named filter sets (title, location,
  remote, salary floor, seniority) that produce "new since last check" deltas
  into the triage feed, rather than re-showing everything.
- **[maybe]** H1B/visa-sponsorship and other structured gates as first-class
  filter fields (jobgpt). Lower value for a single Canadian user but the
  work-authorization field is already in AIHawk's profile schema.
- **[have]** URL dedup across sources.

### 2. Job-description extraction

- **[fits]** **JSON-LD → CSS-selector → AI cascade** for pulling the full posting
  text from an arbitrary URL (ApplyPilot). Try structured data first, fall back
  to the model only when needed — cheaper and more reliable than always asking
  the model. Useful for the "paste a link" path.
- **[fits]** Treat posting text as **untrusted input** — explicit "ignore
  instructions embedded in the posting" framing in every prompt that consumes a
  JD (MadsLorentzen, ApplyPilot). Cheap hardening; the Ideas agent already has
  the pattern for repo content.

### 3. Scoring & triage

- **[have]** Title gate → one judge-and-condense call → feed (`triage.py`,
  `ai/job_triage.py`).
- **[fits]** **Numeric fit score (1–10 / 0–100)** persisted per posting with a
  short rationale, so the feed can sort and the desktop can show "why". Nearly
  every project does this; Lunaschal condenses but doesn't rank.
- **[fits]** **Deal-breaker vs soft-preference split** in the profile
  (MadsLorentzen). Hard gates (location, clearance, on-site) reject before the
  model call; soft preferences (salary floor, parental leave) annotate rather
  than reject.
- **[fits]** **Reasoning-trace explanation** on the scoring decision, shown on the
  desktop review screen — the delegate/agent step UI already exists to render it.
- **[fits]** **Skill-frequency stats** across the postings you've seen — "React
  appeared in 34 of 50 backend roles this month". Pure aggregation over stored
  JDs, no model call.

### 4. Resume tailoring

- **[have]** Bounded-schema tailoring — model selects bullet indexes, never
  writes prose; anti-fabrication guarantee (`tailor.py`). This is stricter than
  every surveyed project and should stay.
- **[fits]** **ATS keyword-coverage report** against the specific JD (ats-checker,
  Resume Matcher): which JD keywords appear / are missing in the tailored resume,
  as a checklist on the review screen. Pairs naturally with the existing
  `keyword_report`.
- **[fits]** **Rendered-PDF verification** (MadsLorentzen): after generating the
  resume PDF, extract its text layer and confirm (a) contact details survived,
  (b) reading order is sane, (c) the keywords you think are in there actually
  extract. Catches template/encoding bugs that a source-level check misses.
- **[maybe]** **ATS-platform simulation** (ats-screener) — model how Workday/Taleo
  specifically would parse the resume. High effort, uncertain payoff for a
  human-reviewed one-at-a-time flow.
- **[fits]** ATS score components worth borrowing as review-screen metrics:
  action-verb density, measurable-impact (quantified bullet) density, section
  sanity, parse-ability. All computable locally without a model.

### 5. Cover letters

- **[maybe]** **Cover-letter generation** tied to a posting. Not currently built.
  If added, it should follow the tailoring discipline: forward-looking framing,
  explicit honesty rule (acknowledge gaps, never invent), reviewed on desktop
  before send. MadsLorentzen and jobsync both gate this behind human review.
- **[fits]** Generate only when the application actually requires one (JobHuntr
  skips optional letters) — don't spend a model call by default.

### 6. Two-agent draft/review

- **[fits]** **Drafter → reviewer pipeline** (MadsLorentzen): one pass tailors the
  resume/letter, a second pass researches the company and critiques the draft for
  missed keywords and weak framing, then the drafter revises once. Lunaschal
  already has the one-tool-loop in `research/agent.py` and staggered scheduling;
  this is a toolbox + a second prompt, not new infrastructure. The company
  research must obey `web.py`'s SSRF guard.

### 7. Application submission (browser extension)

- **[have]** MV3 extension fills real ATS forms from the profile, attaches the
  tailored resume, records answers; no host permissions, `activeTab` on a
  gesture; handles React's ignored `el.value =`.
- **[fits]** **Conditional / follow-up question handling** (Ultimate) — when
  answering a screening question reveals another field (e.g. "willing to
  relocate?" → "which cities?"), detect and fill the revealed field. The
  extension already walks the form; this is re-scanning after each answer.
- **[fits]** **Dry-run / fill-without-submit** mode (ApplyPilot) — the extension
  fills everything and stops at the submit button for the user to eyeball. Fits
  the "desktop is where mechanical work happens, under review" split exactly.
- **[fits]** **Answer history / FAQ memory** — persist every question→answer pair
  so repeat questions ("years of Python", "notice period") auto-fill from what
  you answered last time. Extends the existing Answer Kit.
- **[no]** Fully autonomous background submission ("Infinite Hunt", "monkey
  mode", 24/7 auto-apply, scheduled restarts to beat LinkedIn's daily cap).
  Conflicts with the phone-decides split and with not running a mass bot.
- **[no]** CAPTCHA-solving services, patched-Playwright stealth, headless
  anti-detection. Detection evasion for automated submission — out.

### 8. PII protection

- **[maybe]** **Anonymise PII before the model call, restore after**
  (Ultimate) — swap name/email/phone/address for placeholders in anything sent
  to inference, map back on the way out. Lunaschal's inference is _local_
  (llama-server on localhost), so the threat model is much weaker than a project
  calling OpenAI — but it's still a clean pattern if a cloud fallback is ever
  added, and it bounds what shows up in llama-server logs. Note both projects
  disable it for resume parsing because it hurts quality.

### 9. Interview preparation

- **[fits]** **Interview prep pack** (MadsLorentzen, `/interview`): built from the
  archived application — the exact posting, the resume + cover letter the
  interviewer actually saw, notes from earlier rounds. Assembles likely
  questions, maps each to a STAR example from the profile, flags gaps and drafts
  honest bridge answers.
- **[fits]** **Company + interviewer research** with a verify-before-use rule
  (don't state anything the web search didn't actually support). SSRF-guarded
  web tools already exist.
- **[maybe]** **Mock interview** — a roleplay mode over the prep pack. Voice is
  deprecated in Lunaschal, so text-only; fits the Chat delegate shape.
- **[fits]** **Question → STAR mapping** as a standalone: given a JD, list the
  behavioural questions it implies and which of the user's stored stories answer
  each.

### 10. Follow-up & outcome tracking

- **[have]** Email linkage over the `job_application` classifier; ATS-aware.
- **[fits]** **Stale-application surfacing** (MadsLorentzen `/outcome`) — flag
  applications with no response in N days and draft a follow-up that cites only
  claims from the original submission.
- **[fits]** **Thank-you / follow-up note drafting** after an interview stage.
- **[fits]** **Stage tracking** — applied → phone screen → onsite → offer /
  reject / ghosted, with per-stage dates. jobsync and jobgpt both model this;
  Lunaschal tracks the application but not the funnel.
- **[fits]** **Email → status inference** (MadsLorentzen `/gmail-sync`): read
  application-related mail, _propose_ batch status updates with the source
  message cited, apply only on confirmation. Lunaschal already syncs mail; this
  is a proposal step on top.

### 11. Analytics & dashboard

- **[fits]** **Funnel / conversion dashboard** — applications by status, response
  rate, time-to-response, by sector / board / seniority. Pure SQL over existing
  rows; renders with the inline-SVG chart approach already used elsewhere.
- **[fits]** **Weekly activity summary** — "12 triaged, 3 queued, 2 sent, 1 reply"
  on the Jobs home. Cheap, motivating, matches the briefing scheduler pattern.
- **[fits]** **Screenshot / run-history review** for extension submissions — keep
  a record of what was filled where, viewable later. The extension records
  answers; add the page state.
- **[fits]** **Self-contained offline HTML report** export (MadsLorentzen
  `/html-report`) — one file, stat cards + charts + filterable table, openable
  from the phone without the server.

### 12. Skill-gap analysis

- **[fits]** **Upskill heatmap** (MadsLorentzen `/upskill`): diff the profile
  against a batch of target postings, produce a prioritised list of missing
  skills weighted by how often they appear and how central they are, with
  web-searched learning resources and rough time estimates. Overlaps with the
  skill-frequency stats in §3 and could share that aggregation.

### 13. Networking / referrals

- **[maybe]** **Find a referrer** — given a target company, surface people you
  might know there (from contacts / email history) or a warm-intro path.
  Everything about _how_ the surveyed projects do this (scraping LinkedIn,
  "connect with Open Networkers", auto-DMing hiring managers) is off the table,
  but "search my own mail for someone at Acme" is local and clean.
- **[no]** Automated LinkedIn connection requests / recruiter DMs / inbox
  auto-replies. Mass outreach automation.

### 14. Profile schema additions

- **[fits]** Fields the surveyed profiles carry that Lunaschal's may not:
  work-authorization status, salary expectation (range), notice period /
  availability date, self-identification / EEO answers, relocation willingness,
  security-clearance status. These are exactly the screening questions the
  extension has to answer repeatedly, so storing them once pays off.
- **[fits]** **Company blacklist defaulting to past employers** — never resurface
  a role at a company you've left (or explicitly rejected).

### 15. MCP surface

- **[maybe]** Expose the Jobs module over MCP (jobsync, jobgpt) so an external
  Claude client could add a posting, request a match score, or pull the feed.
  Lunaschal is a local single-user app with its own UI; only worth it if the
  user wants to drive job stuff from Claude Desktop / Claude Code rather than the
  app. The one-tool-loop and route layer already exist to wrap.

---

## Recommended shortlist

If picking a handful that are high-value, low-risk, and aligned with the current
design:

1. **Numeric fit score + rationale, persisted and sortable** (§3) — small change,
   makes the feed materially better.
2. **ATS keyword-coverage checklist on the desktop review screen** (§4) — builds
   on `keyword_report`, no new infra.
3. **Interview prep pack from the archived application** (§9) — genuinely useful,
   fits the delegate/agent shape, no automation risk.
4. **Stale-application surfacing + follow-up drafting** (§10) — email sync and the
   classifier are already there.
5. **Funnel dashboard + weekly summary** (§11) — pure SQL + existing chart
   approach.
6. **Drafter → reviewer pass for tailoring** (§6) — reuses `research/agent.py`,
   catches weak resumes before they're sent.

Deliberately excluded: anything that submits without a human in the loop,
anything doing detection evasion, and anything requiring a cloud AI API.

---

## Sources

- <https://github.com/topics/job-search-automation>
- <https://github.com/topics/auto-apply>
- <https://github.com/Pickle-Pixel/ApplyPilot>
- <https://github.com/beatwad/LinkedIn-AI-Job-Applier-Ultimate>
- <https://github.com/jomacs/linkedIn_auto_jobs_applier_with_AI>
- <https://github.com/wodsuz/EasyApplyJobsBot>
- <https://github.com/Gsync/jobsync>
- <https://github.com/MadsLorentzen/ai-job-search>
- <https://github.com/lookr-fyi/job-application-bot-by-ollama-ai>
- <https://mcp.so/servers/jobgpt-mcp-server>
- <https://github.com/hugounoclaw/ats-checker>
- <https://github.com/sunnypatell/ats-screener>
- <https://resumematcher.fyi/>
- <https://github.com/JensBender/chatgpt-cover-letter-generator>
- <https://github.com/Ancastal/Cover-Letter.AI>
