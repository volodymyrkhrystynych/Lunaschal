# Ideas tab — design doc

**Status: built, and run for real.** Capture, the nightly repo-context agent, the research agent
and its wiki, the tool-using discussion and plan generation all ship, across five commits on
`feat/ideas-research-agent`, and every part of it has now been exercised against a live
llama-server and a real search provider — see [The first live run](#the-first-live-run) for what
that changed. What is still _not_ done is listed under
[Open questions carried forward](#open-questions-carried-forward).

This is the design record. Where the build settled a question, or a decision would be expensive to
re-derive, it is written down here rather than left in the diff.

## Why the tab exists

Feature ideas for Lunaschal lived in [ROADMAP.md](./ROADMAP.md) and [TODO.md](./TODO.md),
maintained by hand. That stopped scaling in two ways. Items got built without the roadmap noticing
— the Paper section already carried a hand-written "Recently fixed" subsection. And there was
nowhere to _develop_ an idea: to research how other people solved it, argue through the trade-offs,
and come out with something a coding agent could execute.

So: an idea inbox you can talk into, an agent that knows what the app already is, and a **Create
plan** button at the end of it.

ROADMAP.md and TODO.md are still the human ledger. The agent reads them, never writes them.

## What the build settled

- **The repo-context agent is mostly not an LLM.** Every fact that matters — routes, tables,
  columns, views, `api.*` namespaces, settings columns — can be read exactly by parsing the
  source. Pushing this repo through a 25 tok/s local model nightly would spend tens of thousands
  of tokens producing a lossy, drifting paraphrase of things extractable in about a second, and
  "is there already a `paper_pages` table?" is precisely the question a summary gets wrong. The
  model's only job is summarizing the git delta, and that column is nullable: a failed summary
  must never cost the facts. See `backend/research/repo_facts.py`.
- **"Already built?" is evidence, not a vibe.** The model never writes a file path — it selects
  evidence _by index_ into a candidate list the server built, and the JSON schema bounds those
  indexes, so llama-server's grammar makes citing a nonexistent file impossible during decoding.
  A deterministic clamp then runs on top. See [Assessment](#assessment).
- **Yielding to the user is a throughput gate, not a mutex.** llama-server has two slots, so a
  background loop and a chat message genuinely run at once — they just halve each other's token
  rate. So background work parks _between_ steps rather than being locked out. See
  [The priority gate](#the-priority-gate).
- **The research loop has no hour window**, unlike every other daemon here. See
  [Scheduling](#scheduling).
- **A sketch is a Paper _page_, not a whole paper**, rendered from the page's existing PNG
  snapshot. No copying, no new storage — the same borrowing `JournalPaperItem` already does.
- **The caption on a sketch is the feature, not decoration.** Vision is off in this project (both
  presets set `mmproj-auto = false`, see `backend/ai/images.py`), so the agent reads the caption
  and the image is for the human. The UI says so out loud. A "describe this sketch" button that
  always errored is the journal-photo-captioning mistake, and it is written down in
  [learnings/ios-voice-memo-capture.md](./learnings/ios-voice-memo-capture.md)'s spirit: don't
  ship the affordance you can't honour.
- **Plans are versioned and append-only.** Regenerating never destroys a version you may already
  have handed to a coding agent.
- **No filesystem writes.** The plan is markdown in the DB with copy-to-clipboard. The app does
  not write into its own working tree.

## The four parts

### 1. Capture

`ideas` keeps `raw_content` and `content` separately, the same contract as `journal_entries`:
what was dictated is never overwritten, and the detail pane falls back to it until an AI-cleaned
version exists, with the transcript still reachable under "As captured".

Dictation **appends to the capture box rather than saving immediately** (`useRecorder`, the
`Learning/BrainDump.tsx` pattern), so a transcript can be corrected — or two thoughts recorded
into one idea — before it becomes a row.

### 2. The repo-context agent

`backend/research/{repo_facts,repo_job,repo_scheduler}.py`, mirroring the briefing's three-file
split (scheduler / job / ai) so the job is directly callable from tests and from a Settings button.

Deterministic extractors, each independently testable:

| Source | Method                                                                                                                                                  |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Routes | An `ast` walk of the `@bp.<method>` decorators, resolving each back to its Blueprint for the `url_prefix`. Not a regex — the prefix is the whole point. |
| Tables | `PRAGMA table_info` on the **live** DB, so every `_ensure_*` migration is included. A static parse of `schema.sql` would miss most recent columns.      |
| Views  | The three hand-synced literals — `VIEWS`, `navItems`, `VIEW_ORDER` — **cross-checked against each other**.                                              |
| API    | Top-level `api.<ns>` keys and their methods in `src/hooks/api.ts`.                                                                                      |
| Docs   | `CLAUDE.md`, `docs/architecture.md`, ROADMAP and TODO: headings indexed, roadmap bullets kept **verbatim**.                                             |

Three details worth keeping:

- **`view_facts` warns when the view lists drift.** Adding a view touches four hand-synced places,
  and one missing from `VIEW_ORDER` simply cannot be reached by the keyboard. The cross-check costs
  one set comparison, so the nightly scan may as well catch it.
- **Snapshot queries order by `generated_at DESC, id DESC`.** `generated_at` is second-resolution,
  so two scans in the same second tie and the ULID is what actually orders them. Without the
  tiebreak, clicking "Scan now" twice could leave the app reading the _older_ snapshot. This was a
  real bug, caught by a test.
- **A blueprint factory's prefix is recorded as `{prefix}`.** `backend/routes/files.py` takes its
  `url_prefix` as a parameter, so it is only knowable at the call site; emitting the bare rule
  would read like a real, mountable path.

Window: **03:00–05:00**, between the chat-title sweep (02:00–03:00) and the briefing (05:00–07:00),
so the three never contend for the two llama slots — and it runs _before_ the briefing, so a
morning briefing sees a current snapshot.

### 3. The research agent and its wiki

`backend/research/{web,wiki,agent,worker}.py`.

**Web access.** `web.py` is the only arbitrary outbound fetch in the app, and **the model picks the
URLs** — which is why the SSRF guard is not optional. The app binds `0.0.0.0` in network mode and
llama-server sits on `localhost:8080`; that is exactly the neighbour an unguarded fetcher hands to
a prompt injection. `assert_public_url` rejects non-http(s), `.local`/`.internal`/`localhost`, and
any host resolving to a private, loopback, link-local, reserved or multicast address — re-run on
**every redirect hop**, with manual redirect following, because a public URL redirecting to
`169.254.169.254` is the whole trick.

> **Known limitation, written down on purpose:** the host is re-resolved when `requests` connects,
> leaving a DNS-rebinding window between check and socket. The per-hop re-check closes the
> practical version of it. Full immunity needs a resolver that pins the validated IP while
> preserving SNI — not worth it for a single-user app on localhost, but read this before reusing
> `web.py` anywhere exposed.

Search is pluggable (Brave / Tavily key, or keyless self-hosted SearXNG). With none configured the
tools return an explanatory _result_ rather than raising, so the loop degrades instead of dying —
the same trust-first stance `backend/ai/learning_verification.py` takes about evidence.

**The wiki is copy-on-write** (`wiki_revisions`, the `learning_revisions` pattern). A background
process editing prose the user relies on has to be auditable and undoable. A `locked` article
rejects agent writes and the pass steps around it.

**Retrieval hands the model the whole index, not just a retriever.** At a few dozen articles,
`wiki_list()` returning `{slug, title, summary}` for everything costs ~1,200 tokens and lets the
model pick by name — that beats any ranking function and costs one SQL query. FTS is the fallback
above `WIKI_INDEX_MAX`, and the escape hatch for re-querying after a miss.

**No embeddings**, deliberately. The `embed` alias has `ctx-size 2048`, so an article would need
chunking, a re-embed-on-edit path and in-Python cosine over an unbounded chunk set. Worse, that
alias is frozen because every `learning_cards` vector lives in nomic's space, so a second consumer
raises the cost of ever changing it. Revisit above ~300 articles.

**The tool loop** (`agent.py`) is a synchronous rewrite of `learning_verification.build_case` —
that one is async because MCP is; these tools are plain functions, so asyncio buys nothing.

- **Tool turns are never streamed.** llama-server reconstructs OpenAI `tool_calls` from Gemma 4's
  native `<|tool_call>` notation via its peg-gemma4 grammar. Reassembling partial tool-call deltas
  across chunks is the kind of thing that works in testing and silently drops an argument in
  production.
- **Gathering and answering are separate turns**, which is what lets the answer stream while
  gathering stays blocking.
- `gather_events` is a generator yielding `('step', …)` then one `('result', …)`; `gather` is the
  blocking wrapper. The SSE route needs the generator — with the blocking form every tool event
  only arrives _after_ gathering ends, which is precisely the silent spinner the events exist to
  replace. This was caught late, by a test stub that exposed the events never streaming.

### 4. Discussion and plans

Discussions reuse `conversations` + `messages`, discriminated by `conversations.idea_id` — a second
discriminator after `writing_project_id`.

> **The trap.** Six queries mean "a general chat conversation" and all of them need
> `AND idea_id IS NULL`. `backend/routes/chat.py:20` is the one that actually leaks: it has no
> `day_key` condition, so idea discussions would appear in the Chat tab immediately. The other five
> are safe only incidentally (they filter on `day_key`, which idea conversations lack) and are
> fixed anyway — the coupling is accidental, and the title sweep silently titling idea discussions
> is exactly the sort of thing that breaks later.

Context is assembled **server-side**, unlike Writing discussions which build their prompt in the
browser from checked notes. The wiki, repo snapshot and assessment are all server data, and a stale
tab should not be able to feed the model an out-of-date picture of the repo.

`render_plan_markdown` is **pure** — no model, no DB — so the model produces structure and never
formatting, and the sections that must be exact (what already exists, which decisions are settled,
which are still open) are stitched in from real rows rather than paraphrased.

## Assessment

`backend/research/evidence.py` → `backend/ai/idea_assessment.py` → `backend/research/assess.py`.

1. **`gather_candidates` is pure and LLM-free.** It builds a numbered list of ≤25 things in the repo
   the idea might already be satisfied by — routes, tables, columns, components, `api.*` methods,
   settings, doc headings — each carrying a real `{kind, ref, file, line}`.
2. **The schema bounds `evidenceIndexes` to that list**, so the model is structurally incapable of
   citing a file that doesn't exist. This is the entire difference between an evidence-backed
   verdict and a confident hallucination.
3. **A deterministic clamp runs after the call:**
   - no citations ⇒ verdict forced to `no`, confidence ≤ 0.4;
   - `yes` with fewer than two citations ⇒ downgraded to `partial`, confidence ≤ 0.6;
   - no repo snapshot ⇒ `no` at 0.0, with a rationale saying to run the scan.

   A confident, uncited "yes" is the one output that could make the user drop an idea they should
   have built. That is what the clamp exists to prevent.

4. **Being on the roadmap is tracked separately from being built.** They are opposites, and
   conflating them is how a backlog item gets marked done because someone wrote it down.
5. **Every assessment records the `snapshot_id` it judged against**, so the UI marks it _stale_ once
   the repo moves rather than presenting an old verdict as current.
6. **`ideas.user_verdict` always wins**, and a human verdict never expires. Recording the correction
   is what keeps the feature trustworthy.

Open questions are upserted by a normalized `question_key`, so a re-run never resurrects one the
user already answered — and answered ones are fed back into the next prompt as settled context.

## The priority gate

`backend/ai/priority.py`. llama-server serves two 24K slots off one set of CPU threads holding the
routed expert tensors. A second concurrent generation does not _block_ an interactive chat message
— it gets the other slot — but it roughly halves its token rate, because both contend for the same
memory-bound expert GEMVs. So this is a **throughput gate, not a mutex**: background work parks
while a human is waiting and resumes shortly after.

- **Nothing preempts a generation already in flight.** That is why `chat_with_tools` gained an
  optional `max_tokens` and the research loop passes a small ceiling: **turn length is the
  granularity at which background work can yield.** Uncapped, a runaway turn could overlap a chat
  message for up to the 1800 s client timeout.
- **The mark is acquired in the view and released in the SSE generator's `finally`.** Acquiring
  inside the generator is wrong — the body doesn't run until Werkzeug pulls the first item, so the
  window between "user pressed Enter" and "first token" would look idle. Releasing outside it is
  wrong too — on client disconnect Werkzeug drops its reference, CPython closes the generator, and
  only a `finally` inside it runs. (That `finally` must not `yield`, or it raises
  `RuntimeError: generator ignored GeneratorExit`.)
- **`MARK_TTL` is the backstop.** `GeneratorExit` is reliable but not guaranteed _prompt_. A leaked
  mark expires, and `wait_for_idle` returns False on timeout with the caller proceeding anyway. The
  worst case is deferral, never starvation.
- **`run_bg` marks its work interactive** in one place, because journal polish and friends were
  triggered by a user action seconds earlier.
- **Long agent runs get their own executor**, never `run_bg`. That queue is one FIFO worker shared
  by journal polish, metadata, attachment transcription, food structuring, workout parsing and
  attempt grading; a multi-minute pass there would head-of-line block seven user-visible flows.

## Scheduling

`backend/research/research_scheduler.py` is **the one daemon with no hour window.** The repo scan,
briefing and title sweep are scheduled at night so they don't compete with the user; this one
defers moment to moment through the gate instead, which is what "runs whenever it likes but yields
to anything you ask for" actually requires. A nightly window would be the wrong shape: research is
long, interruptible, and worth doing while the user is awake and about to read the answer.

Each tick asks four questions and submits **at most one task**: enabled? worker free? user quiet for
`QUIET_SECONDS`? anything due? `QUIET_SECONDS` (30 s) is longer than the gate's own grace, because
kicking off a multi-minute pass the instant a chat finishes is more intrusive than starting one
model call.

`research_job.plan_next` holds the whole policy in one function reading only from the DB, so it is
testable without threads:

- **Assessment always before research** — cheap, no web, and its output is what tells the research
  pass what to look for.
- **Nothing without a repo snapshot**; there'd be nothing to judge against.
- **Nothing without a search provider**, or every pass would just record that the web was
  unavailable.
- **A 24 h per-idea cooldown**, so a fully-assessed backlog doesn't re-research its newest idea
  every two minutes forever.

A failed pass resets `research_state` to `idle` and leaves `researched_at` unset: the idea stays
retryable and is never stranded in `running`, where the planner would skip it permanently.

**`research_enabled` defaults off** — the loop makes outbound web requests, which is not something
to start doing unasked.

## Data model

| Table                              | Notes                                                                                                                                                                                                       |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ideas`                            | `raw_content` (never overwritten) + `content`; `status`; `assessment_id` denormalized to the newest assessment; `user_verdict` / `user_verdict_note`; `research_state` / `research_error` / `researched_at` |
| `idea_sketches`                    | `(idea_id, page_id)` → `paper_pages`, plus the `caption` the agent actually reads                                                                                                                           |
| `repo_snapshots`                   | `facts` (JSON, the deterministic extraction), `digest` (markdown, what agents read), `change_summary` (LLM, **nullable**), `prev_snapshot_id`                                                               |
| `wiki_articles` / `wiki_revisions` | Copy-on-write with `difflib` diffs; `locked` is the user's veto                                                                                                                                             |
| `idea_wiki_links`                  | Which notes belong to which idea                                                                                                                                                                            |
| `idea_assessments`                 | Append-only, each pinned to the `snapshot_id` it judged against                                                                                                                                             |
| `idea_questions`                   | Upserted by `question_key`; `open` / `answered` / `dismissed`                                                                                                                                               |
| `idea_plans`                       | Append-only versions of the rendered markdown plus its source JSON                                                                                                                                          |
| `conversations.idea_id`            | The second discriminator — see the trap above                                                                                                                                                               |

Also `wiki_fts` (external-content FTS5 over title/summary/content/tags, bm25 title-weighted).

**One rule for everything running on the worker:** never hold a transaction across a model or tool
call. `get_db()` hands out one process-global connection with `check_same_thread=False`, so a
`commit()` in any Flask request handler would commit whatever the worker had pending. Write,
commit, then call the model. Existing background writers get away with ignoring this because their
writes are single short statements; a multi-minute agent turn would not.

## The first live run

Run against llama-server (Gemma 4 26B) and Tavily, on a copy of the real DB. Timings on the 8 GB
machine: repo snapshot 18 s, assessment 13-15 s, a research pass 82-88 s, a plan 57-89 s — the
~80 s the plan button blocks for was the estimate, and it holds.

What it settled:

- **Article slugs are topic-shaped, which was the thing worth watching.** A budget-tracker idea
  produced `document-data-extraction-methodologies` and `personal-finance-app-architecture`, not
  one article named after the idea. The "write about the problem space" rule in `WRITE_SYSTEM`
  does the work it was written for.
- **The clamp fires in practice, not just in tests.** The same idea came back with a fluent
  rationale citing journal-attachment machinery and _zero_ structured citations; it was forced to
  `no` at 0.4, exactly as designed.
- **The Chat tab does not leak idea discussions.** Verified against the real conversations table —
  the `idea_id IS NULL` fix on `backend/routes/chat.py` holds with live rows.
- **The SSRF guard holds against live traffic.** `localhost:8080` (llama-server itself),
  `127.0.0.1:5000`, `[::1]`, `169.254.169.254`, `192.168.1.1`, `*.local` and `file://` are all
  refused, and — the one that needed a real network — a public redirector pointed at
  `169.254.169.254` was followed and then rejected at the hop.
- **The model would not open a page — and the reason was not the model.** Worth reading in full
  below, because three separate things had to be fixed and only one of them was a prompt.

### Why it never read a page

The first two passes ran eight searches and opened zero pages, then wrote the articles from
snippets — so `sources` was empty on every article, because provenance is recorded from what was
fetched rather than what the model says it read. Sharpening `GATHER_SYSTEM` ("a snippet is not a
source") changed nothing. Instrumenting the loop to log `finish_reason` and token counts per turn
found three causes, none of them "the model is bad at tool use":

- **The turn budget ran out before the web was reached.** `MAX_TOOL_TURNS` was 6, and Gemma 4 calls
  one tool per turn far more often than it batches. A `wiki_list` and two `wiki_read`s — which
  `GATHER_SYSTEM` explicitly asks for first — spend three of the six before a single search.
  Now 12; a run that ends in a fetch takes 8.
- **A truncated turn was read as a finished one.** Tool-selection turns cost 9-42 completion
  tokens, so `TURN_MAX_TOKENS = 768` is enormous for them — but when the model stops calling tools
  and starts writing a summary, it hits the ceiling and comes back with `finish_reason='length'`
  and no `tool_calls`. The loop's "no tool calls means it's done" check could not tell that from a
  real finish and reported `truncated: False` on a run cut off mid-sentence. `chat_with_tools`
  discarded the finish_reason entirely, so `chat_tool_turn` now returns it and the loop trusts it.
- **The instruction was in the wrong place.** This is the interesting one. Given a URL, the model
  fetches it. Given search results _in the user message_ and told to read one, it fetches. Given
  the same results as a `role: tool` message, with the instruction thousands of tokens back in the
  system prompt, it searches again instead. So the reminder now rides on the search results
  themselves (`web.READ_ONE_REMINDER`) — the position where it demonstrably works.

Together: 4 of 5 passes now open a page and record it as a source, up from 0 of 2. The one that
did not had a search return a dictionary entry and a Spotify page, and declining to read those is
the right call. Pass time is unchanged at 67-96 s, because a fetch replaces a redundant search
rather than adding to it.

Two costs to know about. The loop's last turn spends ~30 s writing a summary that
`flatten_transcript` discards, since it keeps only tool messages — capping it lower would trade
that against the truncation signal above, so it stands. And the discussion endpoint shares this
loop, so a discussion that searches will now also fetch: better grounded, slower to answer.

Three bugs only a live run could produce, all fixed:

- The model was told to cite evidence by candidate number, and carried those numbers into the
  rationale prose too — "stores these in a database (8, 10, 11)" — where the UI renders them as
  pointers to a list the user never sees. `WRITE_SYSTEM`'s sibling rule now forbids it and
  `assess.strip_index_citations` removes them anyway, because one stray `[3, 25]` is all it takes.
- Every plan for a dictated idea was headed **"Untitled idea"**. Voice capture leaves `title`
  empty and, despite the route's comment, nothing ever fills it in; the list papered over it with
  `displayTitle` in `src/lib/ideas.ts` and the server had no counterpart. Now it does —
  `backend/research/idea_text.py`, same clipping rules, so the two agree.
- Phases came out **"1. 1. Database: ..."**: the model numbers them about half the time and the
  renderer numbers them too.
- **One article was written three times in a single pass.** The write-up returned one note's three
  sections as three articles sharing a slug; upserting each in turn left only the last section
  standing, with three revisions behind it. `decide_articles` now keeps the first of a repeated
  slug.
- **A worker thread outlived its test and segfaulted the interpreter.** Not a product bug, but it
  will bite again: the research worker writes through the module-global SQLite connection, and the
  autouse fixture that drains it is torn down _after_ the `client` fixture that closes the
  connection. `client` now stops the worker before taking its database away.

## Open questions carried forward

- **One page per pass is still thin.** Reading is now reliable but shallow: the model opens a
  single page and writes the note from it plus snippets. Whether the right next move is asking for
  two or three reads, or leaving it alone because a wiki note is a starting point and not a
  literature review, wants a few weeks of real notes to judge. `MAX_FETCHES` is 12, so nothing in
  the way but the model's own sense of "enough".
- **The nudge is a prompt, not a guarantee.** `READ_ONE_REMINDER` rides on every non-empty result
  set and works 4 times in 5. If it ever stops working — a model change, a different search
  provider — the structural version is to fetch the top results in code once gathering ends. That
  spends requests on a guess, which is why it was not done first.
- **The wiki has no UI.** Articles are reachable by the agent and in the DB, but there is no way to
  read, edit, lock or revert one from the app. `wiki_revisions` exists precisely so that UI can be
  built; until it is, `locked` can only be set by hand.
- **Discussion and plan generation run inline on the request thread**, not on the worker. Correct
  for a button press, but a plan blocks its request for the ~80 s it takes locally.
- **Research is not targeted by the assessment.** The assessment works out what an idea still needs
  decided, which is exactly what would tell a research pass where to look — but nothing carries it
  across. There is no `researchTopics` field in the assessment schema or on `idea_assessments`
  (an earlier draft of this doc claimed there was), and `plan_next` just researches the whole idea.
- **The 24 h cooldown is a guess.** It exists to stop a tight re-research loop; whether a
  researched idea deserves revisiting daily, weekly, or only when edited is unknown until this has
  run on a real backlog.
- **`gemma4-long` stays unused.** Tempting for whole-repo prompts, but switching aliases makes the
  router unload and reload the 26B — the two presets have opposite tensor placement — so the next
  chat message pays a full model load. It also sets `parallel = 1`, which removes the second slot
  the gate's whole premise depends on.
- **Idea → Writing / Learning handoff** isn't wired. A finished plan is copied by hand.

## Build order

1. **Tab, CRUD, voice capture, Paper sketches** — `96cd30a`
2. **Nightly repo-context agent** — `2aaa05b`
3. **Priority gate, web tools, wiki, agent loop, worker, assessment** — `52b5403`
4. **Agentic discussion and Create plan** — `6b789ab`
5. **The research scheduler** — `e375993`

Each phase landed with its own tests and was verified independently before the next.
