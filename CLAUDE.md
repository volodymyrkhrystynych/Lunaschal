# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Codebase exploration

**Use graphify to explore the codebase.** The code is organized into several interconnected modules (backend AI/scheduling/fanfic/research, frontend React components, daemon loops, API routes). Run `/graphify query "<question>"` to search the knowledge graph instead of grep — it understands cross-module dependencies, data flow, and architectural patterns. The graph is persistent and incrementally updated (`/graphify --update` via `/model haiku`).

## Working conventions

Development happens on two machines: a desktop (comfortable, full mouse/keyboard) and a GPD Pocket 2 — a low-powered handheld with no usable mouse. On the Pocket 2, manual click-through testing is slow and painful, so the workflow leans on branches and automated tests so changes can be verified without a hands-on walkthrough.

### Branch per feature

- **Whenever the user asks for a new feature, start it on a fresh branch** — don't build features on `main`. Create the branch before writing code.
- **Before creating the branch, ask the user for permission and the branch name** — propose a suggested name but let them confirm or override it. Do this during the planning phase (before implementation starts), since the user often switches to auto mode once planning is done and won't be there to approve a branch created mid-implementation.
- Branch naming follows the existing convention: `feat/<short-kebab-description>` for features, `fix/<...>` for bug fixes. Match the style of recent branches (e.g. `feat/voice-command-shortcut`).
- If the user asks for a feature while already on a relevant feature branch, keep working there; otherwise branch off the up-to-date `main`.
- Only commit or push when the user asks.

### Tests are the primary safety net

- Because manual testing is impractical on the Pocket 2, **new features and bug fixes should come with automated tests** that exercise the behavior. Prefer proving a change works with a test over asking the user to click through the UI.
- Both suites are configured and extensive: **pytest** for the backend (`backend/tests/`, config in `pytest.ini`; run `.venv/bin/pip install -r requirements-dev.txt` once) and **Vitest** for the frontend (config in the `test` block of `vite.config.ts`).
- Vitest defaults to the `node` environment for pure-logic tests; component tests (`.test.tsx`) opt into jsdom per-file with a `// @vitest-environment jsdom` pragma and use `@testing-library/react` (auto-cleanup is registered in `src/test/setup.ts`). Extractable logic lives in `src/lib/` precisely so it can be tested without jsdom.
- Favor fast, isolated tests: unit-test AI parsing/classification (`backend/ai/`), route handlers, the FSRS scheduling adapter, pure parsers (`backend/fanfic/xenforo.py`, `backend/meetings/merge.py`), and the DB layer against a temporary SQLite file. Mock external AI providers and network fetches rather than calling them.
- After making changes, run the relevant tests and report the actual results. Treat a green test suite — not a manual walkthrough — as the default bar for "done."
- No ESLint; **Prettier** runs on staged files via a pre-commit hook (`simple-git-hooks` + `lint-staged`). `npm run format` / `format:check` run it manually.

## Commands

```bash
# Development (Flask backend on :5001 + Vite client on :5173)
# Production keeps :5000 — see the Ports note under Key Behaviors.
npm run dev

npm run dev:flask        # backend only (flask --app backend.app run --port 5001 --debug)
npm run dev:client       # frontend only (vite)
npm run dev:desktop      # desktop window via PyWebView pointed at the Vite dev server
python main.py           # build first with npm run build, then serve dist/ + open a window
python main.py --headless # server only, no window (what ops/run-prod.sh runs)

# Local inference (llama.cpp). Prefer the systemd unit — the model takes tens of
# seconds to load, and Flask's --debug reloader restarts constantly.
./llama/start-llama.sh   # llama-server, router mode, llama/presets.ini, :8080
systemctl --user status lunaschal-llama   # see llama/lunaschal-llama.service to install

# Convenience launchers
./start.sh               # kills stale :5001/:5173, warns if llama-server is down, npm run dev
                         # (assumes llama-server is already running; never touches :5000)
./ops/run-prod.sh        # production server, headless (what lunaschal.service runs)
./ops/open-window.sh     # desktop window against the running production server
./start-server.sh        # network mode (NETWORK_MODE=1, requires LUNASCHAL_PASSWORD)
./start-node.sh          # frontend-only on a weak machine; proxies /api to a remote
                         # backend via VITE_API_PROXY_TARGET/LUNASCHAL_URL (Tailscale)

# Voice input listener (Flask app must already be running)
npm run stt              # or ./stt-start.sh

# Tests
npm run test:backend     # pytest (backend/tests)
npm run test             # vitest run (src/**/*.{test,spec}.{ts,tsx})
npm run test:all         # both suites
npm run test:watch       # vitest in watch mode
```

## Architecture

Lunaschal is a single-user personal life-management desktop app with AI integration. Views (in sidebar order): spaced-repetition learning, AI chat, daily tasks + todos, journal, notebook, meeting recorder/transcriber, creative-writing workspace, ideas + research agent, calendar, food log (+ recipes), lifestyle (workouts/heatmap/chores/selfie/calories), fanfic library/reader, newspaper front pages, handwritten paper, file editor, settings. Recipes are no longer a top-level view — they live inside Food (`src/components/Food/RecipeList.tsx`) over the `cookbook` blueprint. Runs as a native desktop window via PyWebView, or as a web app on the LAN in network mode.

### Stack

- **Desktop shell**: PyWebView — `main.py` starts Flask in a background thread then opens a `webview.create_window`
- **Frontend**: React 19 + Vite + Tailwind CSS v4 — in `src/`
- **Backend**: Flask (Python) — in `backend/`
- **API layer**: REST JSON + React Query; typed client in `src/hooks/api.ts` (one `api.*` namespace per feature)
- **Database**: SQLite via Python's built-in `sqlite3`; stored at `./data/lunaschal.db`
- **AI**: local inference only, via **llama.cpp's `llama-server` in router mode** (`llama/presets.ini`, started by `llama/start-llama.sh` or the systemd unit beside it). It speaks the OpenAI API, so the whole `backend/ai/` layer goes through the `openai` SDK pointed at `http://localhost:8080` — one code path, no native-endpoint shim. The model is **Gemma 4 26B A4B** (MoE, 25.2B total / 3.8B active), served with its routed expert tensors in system RAM and everything else in VRAM; that placement is the single biggest performance decision in the project, and [docs/learnings/moe-expert-placement.md](docs/learnings/moe-expert-placement.md) explains why
- `drizzle.config.ts` is vestigial (points at a `server/db/schema.ts` that no longer exists) — the schema source of truth is `backend/db/schema.sql`

### Entry Points

- **`main.py`** — PyWebView desktop launcher. Starts Flask in a daemon thread, waits for `/api/health`, then opens the window. Pass `--dev` to point the window at the Vite dev server instead of the built `dist/` (health-checked on `:5001`, the dev port). **`--headless` skips the window entirely** and runs Flask in the foreground — that's the production server, and `webview`/Qt are imported inside `_start_window()` so this path needs no display. `--server-url` opens a window against an already-running server without starting one.
- **`backend/app.py`** — Flask app factory (`create_app`). Runs DB init, registers all blueprints, mounts auth middleware, serves the built `dist/` in production, restores the sleep inhibitor, snapshots baseline GPU VRAM for the Settings VRAM budget, and (with `STT_LISTENER=1`) spawns the voice listener as a subprocess.

### Backend Structure (`backend/`)

Flask blueprints in `backend/routes/`: `auth`, `journal`, `calendar`, `learning`, `settings`, `chat`, `files`, `writing`, `stt`, `tasks`, `curated_tags`, `shortcuts`, `transcriptions`, `cookbook`, `food`, `fanfic`, `newspapers`, `meetings`, `notebook`, `paper`, `lifestyle`, `ideas`.

Feature-logic packages (kept out of the route files so they can be unit-tested):

- `backend/learning/` — FSRS scheduling adapter (`scheduler.py`), embedding dedup (`dedup.py`)
- `backend/fanfic/` — XenForo parsing/download pipeline, epub/docx import, HTML sanitizing, file storage
- `backend/meetings/` — ffmpeg recording, resumable Whisper pipeline, transcript merging, file storage
- `backend/newspapers/` — frontpages.com scraper, sync, file storage
- `backend/lifestyle/` — the four activity types and per-day heatmap collapse (`activity.py`), exercise-name canonicalization (`exercises.py`), selfie file storage
- `backend/journal/` — file storage for journal audio/photo attachments (`storage.py`)
- `backend/food/` — food-photo storage and EXIF capture-date/GPS extraction (`exif.py`)
- `backend/paper/` — file storage for handwritten page snapshots (`storage.py`)
- `backend/research/` — the Ideas agent: deterministic repo extraction (`repo_facts.py`), SSRF-guarded web tools (`web.py`), the copy-on-write wiki, the sync tool loop, the research worker and the evidence-backed assessment. Design record: [docs/ideas-tab.md](docs/ideas-tab.md)
- `backend/delegate/` — the Chat tab's delegate: the proposal toolbox (`tools.py`), the loop that drives it (`agent.py`), and the decide-delegate-answer glue behind `/api/chat/stream` (`chat.py`). See Chat delegate below
- `backend/tags.py` — shared normalization for JSON-array tag columns (use it, don't grow per-feature rules)

The chat blueprint exposes a streaming SSE endpoint at `POST /api/chat/stream` using Flask's `Response(stream_with_context(...))`.

**There is one tool loop, in `backend/research/agent.py`**, parameterized by `tools=` and `dispatch=`. The Ideas agent and the chat delegate both drive it; a third copy is how the retired `backend/websearch/agent.py` ended up without the `checkpoint()` and the `finish_reason` check that the original had. A new agent supplies a toolbox, not a loop.

Long-running work (fic downloads, curated-tag scans, meeting transcription, Ideas research) runs in daemon threads with an in-memory progress registry; anything that must survive a restart is checkpointed to the DB, and `connection.py` resets orphaned in-flight states (`downloading` fics, `recording`/`transcribing` meetings, `running` idea research) at startup.

There is no cron and no general scheduler: four hand-rolled daemon loops start from `create_app()` (all skipped when `LUNASCHAL_NO_SCHEDULERS` is set, which the test suite does). `chat_title_scheduler` owns 02:00–03:00, `research/repo_scheduler` 03:00–05:00, `briefing_scheduler` 05:00–07:00 — staggered so they never contend for the two llama slots — and `research/research_scheduler` runs in **no window at all**, deferring moment to moment through `backend/ai/priority.py` instead.

### Database Layer (`backend/db/`)

- `schema.sql` — raw SQL `CREATE TABLE IF NOT EXISTS` statements; all IDs are ULIDs; timestamps are unix ints (converted to ISO strings by `row_to_dict`, which also camelCases column names — see `TIMESTAMP_COLS`)
- `connection.py` — opens a single WAL-mode SQLite connection (`get_db()`), runs `schema.sql` on startup, then a long list of `_ensure_*` helpers: **migrations are idempotent ALTER TABLEs guarded by `PRAGMA table_info` checks** — follow that pattern for new columns
- Four FTS5 virtual tables maintained by SQL triggers: `journal_fts`, `recipes_fts`, `fic_chapters_fts`, `wiki_fts`
- Binary/media files live next to the DB under `./data/`: `fanfic/<fic_id>/` (images, PDFs), `meetings/<id>/` (WAV tracks), `newspapers/`, `journal/<attachment_id>/` (entry audio + photos), `lifestyle/<id>/` (daily selfies), `food/<id>/` (meal photos), `paper/<page_id>/` (page snapshots + pasted pictures), plus `shortcuts.json` (in-app key bindings). Roots overridable via `FANFIC_ROOT` / `MEETINGS_ROOT` / `NEWSPAPERS_ROOT` / `JOURNAL_ROOT` / `LIFESTYLE_ROOT` / `FOOD_ROOT` / `PAPER_ROOT` / `SHORTCUTS_PATH`.

### AI Layer (`backend/ai/`)

- `provider.py` — resolves the llama-server URL and model alias from DB settings (`llama_url` / `llama_model`, defaulting to `http://localhost:8080` and `gemma4`). Model names are **router aliases** — section names in `llama/presets.ini` — not file names or Ollama tags
- `llm.py` — shared generation helpers over llama-server's OpenAI API: `chat_json`, `chat_text`, `chat_messages`, `chat_stream_deltas`, `chat_stream_events`, `chat_with_tools`. Three things to know: **`chat_json` takes a `schema=`** (JSON Schema) which llama-server compiles to a GBNF grammar — every call site passes one, and closed vocabularies like the journal tag list are enforced by the grammar rather than requested in the prompt; **thinking is a boolean, not a level** (`enable_thinking` via `chat_template_kwargs`), sent explicitly because Gemma 4's template defaults it _on_; and **reasoning arrives two different ways** — as a `reasoning_content` delta field or inline in a `<think>` block — depending on the server's flags, not the request, so `chat_stream_events` handles both and labels them `('thinking', …)` apart from `('content', …)`. The inline case is tracked with a running flag rather than a regex, because the tags arrive split across chunks. `chat_stream_deltas` is now a filter over it, so every existing caller still gets the answer alone. There is deliberately **no per-request context window** — llama-server fixes it at load time
- `chat.py` — system-prompt assembly (journal + schedule context, time stamping) and `chat_stream`, still used by the STT transcript-cleanup route
- `chat_title.py` — nightly conversation titling
- `embeddings.py` — text embeddings for Learning answer-dedup, via the `embed` router alias. Still **nomic-embed-text-v1.5** on purpose: the float32 vectors already stored on `learning_cards` are compared by cosine similarity, so a different embedding model would silently invalidate every stored vector
- `journal.py` — entry polish/metadata (tags constrained to the closed `JOURNAL_TAGS` vocabulary by schema enum); `classify_entry_for_tag(content, tag_name) -> bool` for the curated-tag background scan
- `learning_generation.py` / `learning_grading.py` / `learning_verification.py` — flashcard generation, claim-coverage grading, MCP-grounded verification (see Learning below)
- `mcp_client.py` — asyncio bridge to the `mcp` SDK (per-request sessions, stdio/http transports), MCP→OpenAI tool mapping
- `writing.py` — `summarize_discussion` for the Writing module
- `meetings.py` — meeting-transcript summarization (keeps the transcript tail; returns None when AI unconfigured — never fails the pipeline)
- `recipes.py` — recipe extraction from pasted text or scraped page text → `{title, content, tags}` JSON
- `workouts.py` — freeform gym log → `[{name, sets: [{weight, reps}]}]`; returns None on any failure so the route keeps the raw text and can re-run the parse

### Auth (`backend/auth.py`)

Single-user auth via JWT cookie (`lunaschal_token`, 30-day expiry). **Auth is only enforced in network mode** (`NETWORK_MODE=1`) and only for non-localhost requests — the `check_auth` middleware in `app.py` returns early when `is_localhost(request)` is true. A matching `X-Lunaschal-Password` header also bypasses the cookie (used by the STT listener when it runs on another machine). Network mode login requires both the password and a rotating 6-digit display code (pseudo-2FA); the code is stored in the `settings` table and can be regenerated from the Settings page.

### Frontend Structure (`src/`)

- `App.tsx` — top-level view router; checks auth status on load, shows `Login` if unauthenticated in network mode, otherwise sidebar + main view + the persistent bottom `SttPanel`
- `src/components/` — one file (or subdirectory) per view: `Chat/` (`ChatPanel` + the shared `AgentSteps` / `ReasoningBlock` / `ThinkingLabel`), `ChatNav`, `Tasks`, `Journal`, `Meetings`, `Writing/`, `Calendar`, `Learning/`, `Cookbook`, `Fanfic/` (library + folders + reader), `Newspapers`, `Editor/` (file editor + STT panel), `Settings` (+ `CuratedTagsSection`, `ShortcutSettings`)
- `Chat/AgentSteps.tsx` is shared with the Ideas discussion, and `src/lib/agentSteps.ts` holds the one `stepLabel` both use — each had grown its own copy of the `<details>` block and the labeller. An agent trace renders **collapsed even while streaming**: a growing list of steps used to push the reply down the page as it was being read
- `src/hooks/api.ts` — typed REST client (`api.*` namespaces) using plain `fetch`; no tRPC
- `src/lib/` — pure logic extracted for node-environment tests (todo sorting, tag parsing, journal feed grouping, font-size steps, fanfic helpers, VRAM thresholds…)
- `src/shortcuts/` — the in-app keyboard system (see below)
- `@` path alias resolves to `./src/`
- CSS custom properties (e.g. `var(--color-bg)`) are used for theming throughout

### In-app keyboard shortcuts (`src/shortcuts/`)

Keyboard-first, single-key navigation (the Pocket 2 has no usable mouse): WASD-style `nav.up/down/out/in`, `N` new item, `B` sidebar, plus per-view actions — all `ActionId`s and defaults in `keymap.ts`. Bindings are user-editable in Settings → Shortcuts and persisted server-side (`GET/PUT /api/shortcuts` → `data/shortcuts.json`). `ShortcutProvider` owns the global keydown listener (skipping editable targets), view cycling, and numbered **shortcut scopes** for list navigation — a scope number must be registered only once per mounted tree (last registration silently wins; the Writing nav is the canonical single-scope-2 owner). The number row is deliberately unbound for tabs — it belongs to Learning review ratings. This browser-side keymap (KeyboardEvent.code combos) is separate from the evdev key names the OS-level STT listener uses; `ShortcutProvider` maps evdev combos from settings so the listener's keys can be shown/avoided.

### Feature modules

#### Chat delegate (`backend/delegate/`, `src/components/Chat/`)

The Chat tab is one conversation with one endpoint. The main chat model carries **exactly one tool, `delegate({task})`**, so its only decision is the binary "does this need a hand-off"; every capability lives in the delegate's toolbox instead, which is what keeps the main chat's context from growing a token of JSON schema each time the app learns to do something new. Things to know:

- **It replaced a post-hoc classifier, and the failure mode is the point.** `backend/ai/classifier.py` ran _after_ the reply, gated by a hand-rolled `should_classify` heuristic, and turned every exception into a fake low-confidence `conversation` result — so a request that failed produced no reply, no error and no log line, indistinguishable from a message never meant as a request. The decision now happens before the reply, the model makes it, and a failure is a visible error on the stream.
- **The delegate proposes; it never writes.** `propose_task` / `propose_calendar_event` / `propose_calorie_log` / `propose_note_to_self` / `propose_flashcards` stage a payload and return "nothing has been saved yet" to the model, which is what stops the reply claiming it's done. `web_search` / `web_fetch` do run for real.
- **A proposal rides its own step event** (`event['proposal']`), so the shared loop needs to know nothing about proposals — it already collects events, and the caller filters. Proposals are never parsed back out of the model's prose, the same stance `research/agent.py` takes about recording sources from what was actually fetched.
- **A proposal is a durable row, not a live event.** When a run started via `backend/delegate/runs.py` finishes, every staged proposal except `note` (which drafts flashcards immediately with no confirm step — the draft _is_ the review) gets a stable id and `status: 'pending'` written into the assistant message's metadata. `POST /api/chat/proposals/<message_id>/<proposal_id>` (`{action: 'accept'|'dismiss'}`) is the only place a proposal ever leaves `'pending'` — same shape as `decide_briefing_todos`, and it's what makes a confirm card survive a reload or a dropped connection instead of living only in the browser's React state. Accepting dispatches by `kind` to a small per-kind writer (`_accept_calendar`/`_accept_calorie`/`_accept_task`/`_accept_flashcards` in `backend/routes/chat.py`) and stores its result back onto the proposal, so the resolved state renders from `metadata` alone on the next load.
- **Only the delegate's closing summary crosses back into the main conversation**, never its transcript — that compression _is_ the point of delegating. A truncated run doesn't get to pass off its half-sentence as that summary (`agent._summary`).
- **The decision turn is separate from the answer and its prose is discarded.** Tool turns can't be streamed (llama-server reconstructs `tool_calls` from a grammar; reassembling partial deltas drops arguments), so the turn that may carry a tool call is blocking, capped at `DECISION_MAX_TOKENS`, and told to emit nothing when no hand-off is needed. The answer then streams off a prompt llama-server has already cached, so the restart costs generation, not prefill.
- **A caller-supplied `systemPrompt` turns the delegate off** (`delegate=False`). The voice listener, task nudges, the morning check-in and Writing discussions all post to `/api/chat/stream` with their own prompt, and they have no card to confirm anything on — for them the decision turn would be pure latency before a spoken word.
- **A `conversationId` in the body means the reply generates on a background thread** (`backend/delegate/runs.py`), independent of the request that asked for it — the Chat tab is the only caller that sends one. `priority.begin('chat.stream')` moves into that thread with it, acquired when the run starts and released in its `finally`, rather than living in the view/generator the way `ideas.discuss` still does. Callers with nowhere durable to put a reply (voice, nudges, morning check-in, Writing discussions) keep the original inline path: no `conversationId`, one mark spanning the view's generator exactly as before.
- SSE carries five kinds under the one `data:` frame: a bare `{tool: …}` step, `{thinking: …}`, `{content: …}`, `{messageId: …}` (persisted path only, first frame), and a final `{done: true, steps, sources, proposals}`. Steps and (on the persisted path) proposals are also written into the message's metadata by the background thread itself, not by the browser — a reload reads the same trace straight from the row instead of needing the live events to have arrived first.
- **Reasoning is shown collapsed and never persisted** (`ReasoningBlock`) — it's the model talking to itself, it dwarfs the reply, and it's rendered as plain pre-wrapped text because half-finished markdown in a reasoning trace turns into headings the model never meant.
- The **Web Search tab is gone**: searching is something the delegate decides to do mid-conversation, so making the user pick the mode up front meant asking them to predict the answer before asking the question. Old `mode='websearch'` conversations still render — they persisted the same `{steps, sources}` shape `parseAgentMeta` reads — but none can be started. There is now **one search provider for the whole app** (`research_search_*`, Settings → Research); `_migrate_websearch_search_to_research` folds a config made under the old tab into it once at startup.

#### Learning (`backend/routes/learning.py`, `src/components/Learning/`)

AI-augmented spaced repetition. All generated cards (brain-dump, journal, chat topic, verification follow-ups) land as `pending` in ONE approval queue (approve / steerable-regenerate / deny); scheduling is **FSRS** via the `fsrs` package (`backend/learning/scheduler.py`; `fsrs_state=NULL` = never reviewed/reset). A review session hands out `DECK_SIZE` (10) due cards and is **resumable**: every answered or flipped card is persisted to `learning_attempts` (one open row per card, deleted in the same transaction as its rating), so leaving the view or reloading never re-asks a card. `/due` sorts open-attempt cards first so a resumed deck always contains them; the client seeds its session from `/due` + `/attempts` and keeps local state authoritative from there. Grading is claim-coverage: cached claim decomposition → coverage check → pre-selected Again/Hard/Good/Easy the user can override. It runs **after** the answer is saved, on `backend/ai/background.py`'s single-worker executor (`backend/learning/attempts.py`) so it queues below interactive chat rather than blocking the submit; the result lands on the attempt row and the client polls it in. Answer embeddings live as float32 blobs on `learning_cards` (in-Python cosine, `backend/learning/dedup.py`) powering the approve-time duplicate **hint** (never auto-reject) and the low-similarity grading gate; both silently disable without an embedding provider. Folders bind at most one MCP evidence provider (`mcp_servers` registry) for verification — trust-first: no provider/no hit ⇒ "no authoritative source found", never open-web. Revising an active answer retires the card (`revised_from` links versions, append-only `learning_revisions` log) and resets FSRS only for semantic changes. Deletes are hard deletes; FKs null `derived_from`/`revised_from` breadcrumbs.

#### Writing (`src/components/Writing/`, `backend/routes/writing.py`)

Two-panel layout: left nav (project list + a `WritingNav` with Chapters/Notes/Discussions sections) | full-width center panel that switches on the selected item: chapter → prose editor, note → note editor, discussion → chat view.

**DB tables**: `writing_projects`, `writing_chapters` (ordered by `position`), `writing_context_docs` (typed: `character | outline | worldbuilding | note`). "Notes" in the UI/API are stored in `writing_context_docs` (HTTP paths are `/api/writing/.../notes`; the table name is legacy). Discussions reuse the existing `conversations` + `messages` tables; `conversations.writing_project_id` scopes them to a project, and the general Chat tab filters them out (`writing_project_id IS NULL`). Deleting a project deletes its discussions.

**Chapter/note editors**: plain `<textarea>` (not CodeMirror — prose, not code) with 1.5 s debounced auto-save; chapters add live word count and font-size shortcuts. **Discussions**: full-size chat reusing `/api/chat/stream` unchanged; the frontend assembles a `systemPrompt` from the project plus checked notes. A **Summarize** button distills the transcript into a new note via `backend/ai/writing.py`.

#### Ideas (`backend/routes/ideas.py`, `src/components/Ideas/`)

The app's own feature backlog, developed with an agent instead of by hand in `docs/ROADMAP.md`. Master-detail: list + capture box on the left, idea detail on the right. Design record and the decisions the build settled — including what is deliberately _not_ built: [docs/ideas-tab.md](docs/ideas-tab.md). Things to know:

- **An idea keeps `raw_content` and `content` separately**, the same contract as `journal_entries`: `raw_content` is what was dictated or typed and is never overwritten; `content` is the AI-cleaned prose. The detail pane shows `content` when it exists and falls back to `raw_content`, with the transcript still reachable under "As captured".
- **Dictation appends to the capture box rather than saving immediately** (`useRecorder`, the `Learning/BrainDump.tsx` pattern) so a transcript can be corrected, or two thoughts recorded into one idea, before it becomes a row.
- **A sketch is a Paper _page_, not a whole paper** (`idea_sketches` → `paper_pages`), rendered straight from the page's PNG snapshot at `/api/paper/pages/<id>/image` — no copying and no new storage, the same borrowing `JournalPaperItem` does. Deleting the page cascades the sketch.
- **The caption on a sketch is the feature, not decoration.** Vision is off in this project (both presets set `mmproj-auto = false`; see `backend/ai/images.py`), so the agent reads the caption and the image is for the human. The UI says so out loud — a "describe this sketch" button that always errored is the journal-photo-captioning mistake.
- `page_image_url` in `backend/routes/paper.py` is exported (not `_`-prefixed) precisely because Ideas borrows it; keep it that way.

**The repo-context agent** (`backend/research/repo_facts.py`, `repo_job.py`, `repo_scheduler.py`) maintains a nightly `repo_snapshots` row describing what the app currently is, so "you already built this" is evidence rather than a guess.

- **It is mostly not an LLM.** Routes come from an `ast` walk of the `@bp.<method>` decorators (which resolves each back to its Blueprint for the `url_prefix`), tables from `PRAGMA table_info` on the **live** DB (so `_ensure_*` migrations are included, which a parse of `schema.sql` would miss), views from the three hand-synced frontend literals. Pushing the repo through a 25 tok/s model nightly would cost tens of thousands of tokens to produce a lossy paraphrase of things we can read exactly. The model's only job is summarizing the `git log` delta — and it is nullable, because a failed summary must never cost the facts.
- **`view_facts` cross-checks `VIEWS` / `navItems` / `VIEW_ORDER`** and emits a warning when they drift. Those three are maintained by hand in three files, and a view missing from `VIEW_ORDER` simply can't be reached by the keyboard.
- **`CLAUDE.md`, `docs/architecture.md`, `docs/ROADMAP.md` and `docs/TODO.md` are read, never regenerated.** ROADMAP/TODO bullets are kept verbatim — they are the "planned but not built" ledger.
- Snapshot queries order by **`generated_at DESC, id DESC`**: `generated_at` is second-resolution, so the ULID is what actually orders two scans in the same second.
- Window defaults to 03:00–05:00, between the chat-title sweep (02:00–03:00) and the briefing (05:00–07:00), so the three never contend for the two llama slots.

**The research agent** (`backend/research/web.py`, `wiki.py`, `agent.py`, `backend/ai/priority.py`):

- **`backend/ai/priority.py` is a throughput gate, not a mutex.** llama-server has two slots, so a background loop and a chat message genuinely run at once — but they share memory-bound expert tensors, so the loop parks _between_ steps while a human waits. Nothing preempts a generation already in flight, which is why `chat_with_tools` now takes `max_tokens` and the research loop passes a small ceiling: **turn length is the granularity at which background work can yield.** The mark is acquired in the _view_ and released in the SSE generator's `finally` — acquiring inside the generator would leave time-to-first-token looking idle, and releasing outside it would leak on client disconnect. `MARK_TTL` and a `wait_for_idle` timeout mean a leak costs deferral, never starvation.
- **`run_bg` marks its work interactive** in one place, because journal polish and friends were triggered by a user action seconds earlier. Long agent runs deliberately do _not_ go on that executor — they would head-of-line block seven user-visible flows.
- **`web.py` is the only arbitrary outbound fetch in the app, and the model picks the URLs.** `assert_public_url` rejects non-http(s), `.local`/`.internal`, and any host resolving to a private/loopback/link-local/reserved address — re-run on every redirect hop, with manual redirect following. Search is pluggable (Brave/Tavily key, or keyless self-hosted SearXNG); with none configured the tools return an explanatory _result_ rather than raising, so the loop degrades instead of dying.
- **The wiki is copy-on-write** (`wiki_revisions`, the `learning_revisions` pattern) — a background process editing prose the user relies on has to be auditable and undoable. A `locked` article rejects agent writes. Retrieval hands the model the whole index (`wiki_list`) rather than only a retriever: at a few dozen articles that costs ~1,200 tokens and beats any ranking function. **No embeddings** — the `embed` alias has `ctx-size 2048` so articles would need chunking, and its vectors are frozen because `learning_cards` depends on them.
- **Tool turns are never streamed.** llama-server reconstructs OpenAI `tool_calls` from Gemma 4's native notation via its peg-gemma4 grammar; reassembling partial tool-call deltas across chunks is how an argument goes missing in production. Gathering and answering are separate turns so the answer can stream while gathering stays blocking. `agent.gather_events` is a generator yielding `('step', …)` then one `('result', …)`, and `agent.gather` is the blocking wrapper — the SSE route needs the generator, because with the blocking form every tool event only arrives _after_ gathering ends, which is the silent spinner the events exist to replace.

- **The research loop is the one daemon with no hour window** (`backend/research/research_scheduler.py`). The repo scan, briefing and title sweep are scheduled at night so they don't compete with the user; this one defers moment to moment through `priority` instead, which is what "runs whenever it likes but yields to anything you ask for" actually needs — and research is worth doing while the user is awake and about to read the answer. Each tick asks four questions (enabled? worker free? user quiet for `QUIET_SECONDS`? anything due?) and submits at most one task. `research_job.plan_next` holds the whole policy: assessment always before research (cheap, no web, and its output is what tells the research pass what to look for), nothing without a repo snapshot, nothing without a search provider, and a 24 h per-idea cooldown so a settled backlog doesn't re-research its newest idea every tick. **`research_enabled` defaults off** — the loop makes outbound requests.

**Assessment — "already built?" is evidence, not a vibe** (`backend/research/evidence.py`, `assess.py`, `backend/ai/idea_assessment.py`):

- **The model never writes a file path.** `gather_candidates` builds a numbered list of things in the repo the idea might already be satisfied by, each with a real `{kind, ref, file, line}`, and the JSON schema bounds `evidenceIndexes` to that list — so llama-server's grammar makes citing a nonexistent file impossible during decoding.
- **A deterministic clamp runs after the call**: no citations ⇒ verdict forced to `no` (confidence ≤ 0.4); `yes` with fewer than two citations ⇒ downgraded to `partial`. A confident uncited "yes" is the one output that could make the user drop an idea they should have built.
- **Being on the roadmap is tracked separately from being built** — they're opposites, and conflating them is how a backlog item gets marked done because someone wrote it down.
- Each assessment records the `snapshot_id` it judged against, so the UI marks it **stale** once the repo moves rather than presenting an old verdict as current. `ideas.user_verdict` always overrides the agent's.
- Open questions are upserted by a normalized `question_key`, so a re-run never resurrects one the user already answered.

**Discussion and plans**: `conversations.idea_id` is a second discriminator after `writing_project_id` — **six queries** filter "a general chat conversation" and all of them need `AND idea_id IS NULL` (`backend/routes/chat.py:20,30,51,81`, `briefing_job.py`, `chat_title_scheduler.py`). `backend/research/plan.py::render_plan_markdown` is pure, and the sections that must be exact — what already exists, which decisions are settled, which are still open — are stitched in from real rows rather than paraphrased by the model.

#### Fanfic library (`backend/routes/fanfic.py`, `backend/fanfic/`, `src/components/Fanfic/`)

Personal fanfiction library + reader ("Library" in the UI). Imports from XenForo forums (SpaceBattles / Sufficient Velocity / Questionable Questing) by scraping threadmark reader pages — `xenforo.py` is a **pure parser** (no network/DB; tests feed fixture HTML), `download.py` streams chapters into the DB one reader page at a time (resumable; in-memory progress registry; 2 s request delay; browser UA + per-domain cookies from `site_cookies` for Cloudflare). Also imports epub/docx uploads and stores PDFs. Chapters keep sanitized HTML + plain text (FTS). Per-fic: folders (ordered), site tags, per-chapter read tracking, last-read position, rating/review, update checking (`check-updates` / `refresh-alerts` set `update_pending`; a single drain worker walks the flags one fic at a time). Journal entries can reference fics/chapters (`journal_entry_fic_refs`) — reading commentary shows up in the Journal feed and deep-links back into the reader.

**Update checks come in two tiers, because an edit is invisible from outside the post.** XenForo raises no alert when an author revises an existing chapter and leaves the threadmarks index untouched, so nothing about the fic looks different until you re-read the post itself.

- A **cheap** check looks only for chapters we don't have. It diffs the threadmarks index's post ids against the stored ones and resumes at the reader page holding the first missing chapter — one index fetch per ~50 threadmarks per category, and no reader fetch at all when nothing is missing. Three things about it are load-bearing, and all three were bugs:
  - **`Statistics (N threadmarks)` is never used to skip a category.** It counts a different population than our rows do — threadmarks get recategorised, renamed and deleted on long threads — so the two drift apart, and every count-based shortcut fails in one of two ways. `count <= rows` latched a fic shut permanently the moment the site's count fell below ours (`test_check_updates_survives_the_site_losing_a_threadmark`): this is the root cause of recently-updated fics never downloading, and a category losing a couple of _non-chapter_ threadmarks is enough to trigger it. `count == rows` then still agreed a category was current when it had swapped two threadmarks for two others (`test_check_updates_sees_swapped_threadmarks_at_an_unchanged_count`). Post ids are the only comparison that can't be fooled.
  - **The resume page comes from the index position, never from our row count.** Count arithmetic overshoots whenever there's a gap, so a chapter missing from the middle pushed the walk past the very page holding it and stayed missing forever.
  - Chapters the site un-threadmarks are **kept**, not deleted — we downloaded them, and the site dropping a threadmark isn't a reason to destroy the reader's copy.
- A **deep** check walks every reader page and compares each post against the saved chapter. `fic_chapters.edited_at` holds XenForo's "Last edited" timestamp — parsed from `.message-lastEdit`, which sits in the same `<article>` the body does, so it costs no extra request. A changed chapter is rewritten **in place**: `position` is never touched, or a typo fix would reshuffle reading order and the last-read pointer.
- **Deep only ever runs when asked** (`{"deep": true}`, the Deep button; `fics.deep_pending` carries the request to the drain worker). There is deliberately no cadence and no auto-escalation: authors revising already-published chapters is rare, so re-walking every fic on a timer would spend far more requests than it recovers.
- **`edited_at` is left NULL by the migration on purpose.** A post with no edit notice also parses to `None`, so unchanged chapters compare equal and a first deep scan doesn't rewrite the whole library.
- **`refresh-alerts` no longer skips a fic for being fetched more recently than its alert.** That comparison assumed an alert is the only way a thread changes; with edits raising none, "checked since the alert" regularly meant reporting a fic current while a revised chapter sat unread.

#### Meetings (`backend/routes/meetings.py`, `backend/meetings/`, `src/components/Meetings.tsx`)

Records two PulseAudio/PipeWire streams via ffmpeg — mic + default sink `.monitor` (system audio) — one meeting at a time; optional echo-cancel via PipeWire's module in monitor mode (failure falls back to raw mic — EC must never cost a recording). Uploads are transcoded to the system track. Transcription is a background pipeline (`pipeline.py`): user picks Whisper model/device → chunked resumable transcription (checkpointed to the `meetings` row after every 30 s chunk; pausable, survives restarts) → pyannote diarization of the system track when an `hf_token` is set → `merge.py` (pure, ML-free: echo-bleed stripping, speaker labeling, coalescing) → AI summary. Progress lives in the `phase` column; `status='error'` preserves `phase` as the resume point for `/retry`.

#### Cookbook (`backend/routes/cookbook.py`, `backend/ai/recipes.py`, `src/components/Cookbook.tsx`)

Recipe collection. Paste text or a URL — the page is fetched and stripped, then `parse_recipe` extracts title/markdown-content/tags via LLM JSON mode. FTS search (`recipes_fts`), tag filtering.

#### Tasks & todos (`backend/routes/tasks.py`, `src/components/Tasks.tsx`)

Two lists in one view: **daily tasks** (max 4, per-day completions in `daily_task_completions`, reset each day) and one-off **todos**. The STT listener runs a **task-nudge loop**: on an interval (Settings → nudges, default 45 min, waking-hours window) it picks a pending daily task and starts a short spoken check-in conversation about it.

#### Lifestyle (`backend/routes/lifestyle.py`, `backend/lifestyle/`, `src/components/Lifestyle/`)

Workouts, activity heatmap, progression charts, chores, daily selfie, calories — one scrollable column. Design record and the decisions the build settled: [docs/lifestyle-tab.md](docs/lifestyle-tab.md). Things to know before touching it:

- **Chores are not a new table.** They're `todos` rows with `list='chores'`, so the Lifestyle section and the Tasks view edit the same rows through `/api/tasks/todos`.
- **Workout entry is freeform text, parsed in the background** (`backend/ai/workouts.py` → `run_bg`), same pattern as the food/recipe extractors. `raw_text` is never overwritten and `parse_status` tracks the attempt, so a bad parse is retryable via `POST /workouts/<id>/reparse` rather than lost.
- **Exercise names fold one way only** (`backend/lifestyle/exercises.py`): an abbreviation folds onto a known fuller name, but a more specific name starts its own series. Over-merging can't be undone; splitting costs one `POST /exercises/merge`.
- **The workout form mirrors itself to `localStorage`** (`src/lib/workoutDraft.ts`) — logging happens mid-set on a phone, and a backgrounded tab reload would otherwise wipe the textarea.
- **The heatmap's four activity colours were validated, not chosen by eye** — CVD separation and contrast against both surfaces. Re-run the check if they change; identity is never colour-alone (legend + per-day labels).
- **No charting library**: geometry is pure functions in `src/lib/lifestyle.ts`, rendered as inline SVG.
- Selfie images live under `./data/lifestyle/<id>/` (`LIFESTYLE_ROOT`), one per day, never as blobs. **Take selfie goes through the native camera** (a hidden `capture="user"` file input), not `getUserMedia` — the in-page preview was wrong on a tablet. And the history strip is **read-only**: tapping a thumbnail previews it, never deletes. Replacing a day means retaking it that day; removing one is a manual DB edit, because an accidental tap used to destroy a selfie outright.
- **Workout intensity is 1-5 stars, not 1-10 RPE** (`INTENSITY_LABELS` in `src/lib/lifestyle.ts`, `IntensityStars.tsx`): a ten-point scale was too subjective to answer honestly. The five labels are the feature — surface them, and never render the rating as glyphs alone. Old 1-10 values were folded once by `_migrate_workout_intensity_to_stars`, latched on the existence of the `settings.workout_intensity_five_star` column.
- **Bodyweight sets carry `weight: null`**, explicitly required by the parser's JSON schema rather than omitted ("squats 10 10 10 10" is four sets of ten). Anything consuming set weights has to render that as bodyweight and keep it off the weight charts — `Progression.tsx` plots total reps instead.

#### Newspapers (`backend/routes/newspapers.py`, `backend/newspapers/`)

Archives daily front pages (Toronto Star, NYT) from frontpages.com. The scraper decodes the base64-inlined image URL (the `og:image` is a decoy) and **dates editions by the date embedded in the image URL, not the local clock** — the site can serve yesterday's edition past midnight. `POST /api/newspapers/sync` is idempotent per (paper, date).

#### Transcriptions (`backend/routes/transcriptions.py`)

Append-only log of everything the STT pipeline transcribed (source/app/detail). The Journal feed can interleave them between entries (`src/lib/journalFeed.ts`; transcriptions are visible but not selectable).

### Key Behaviors

- **Curated tags** — user-defined tags managed in Settings → Tags tab. Each new tag triggers a background daemon thread that calls `classify_entry_for_tag` per journal entry and writes matches to `journal_entry_curated_tags`. Progress tracked in-memory (`_scan_progress` dict in `curated_tags.py`); the list endpoint merges it in. Tags appear as filter pill buttons in the Journal view; entries display curated tags (`#name`, neutral style) separately from freeform AI tags (accent color).
- **Journal entries** keep `raw_content` (as typed/spoken) alongside AI-polished `content`; polish and metadata generation run as background threads after save. The Journal feed also interleaves fic-reading commentary via `journal_entry_fic_refs`. `polish_journal_entry` **raises `PolishUnavailable` rather than falling back to the raw text** — the two used to be indistinguishable, so an offline llama-server overwrote a polished entry with its transcript and returned 200; the manual Polish route now answers 503 and leaves `content` alone.
- **Journal attachments** (`journal_attachments`, `backend/journal/storage.py`, `src/components/JournalAttachments.tsx`) — audio, video and photos hung off an entry, added/renamed/deleted in the entry's edit mode and playable/readable outside it. Files live under `./data/journal/<attachment_id>/` (`JOURNAL_ROOT`), never as blobs, and uploads **stream to disk** (`file.save`, not `read()`) because a phone video is happily several hundred MB. Things to know:
  - **Paste and drop are first-class, not a convenience.** The capture happens on the whole editor (`JournalAttachments` wraps the title/body fields as its own paste target) because the media is recorded on a phone or iPad, where exporting to Files and picking it back out is the step being deleted. A paste carrying no media falls through to the textarea untouched. The compose box for a _new_ entry stages files in local state instead — the entry has no server-side row yet — and uploads them from `submitNewEntry`'s per-call `onSuccess`. **Both work on the desktop and neither works on iOS** — getting a Voice Memos recording in is unsolved, and [docs/learnings/ios-voice-memo-capture.md](docs/learnings/ios-voice-memo-capture.md) records the dead ends (and why iOS Shortcuts is the next thing to try) so they aren't re-walked. An upload with an empty filename is legitimate and must stay supported either way.
  - **`kind` is `audio | video | image`, and mime beats extension** (`storage.resolve_upload`): mp4/webm carry either, so only the upload's mime type separates a voice memo from a clip. With no usable mime an ambiguous extension defaults to video — audio in a `<video>` costs a blank frame, the reverse throws the picture away.
  - **Video takes the speech path, not the vision one.** ffmpeg pulls the audio track out of the container, so `_do_attachment_audio` handles it unchanged.
  - The user-given `name` is the point (an attachment is "what this is about", not a filename), and **transcription/captioning is opt-in per attachment, never on upload** — it costs a real CPU transcription here, and a voice memo is often kept as audio. Both run on `run_bg` and report through `transcript_status`, with results pushed over the existing `/api/journal/events` SSE stream.
  - Photo captioning additionally needs `llama_vision_model` set (Settings → llama.cpp): both chat presets set `mmproj-auto = false` because the ~1.1 GB vision tower doesn't fit next to the 26B, so it ships **off** and says so — see `backend/ai/images.py`.
- **Paper pages are a fixed A4 sheet** (`PAGE_WIDTH`/`PAGE_HEIGHT` = 2100×2970 tenths of a mm in `src/lib/paper.ts`), contain-fit into the viewport with bars on the short axis — never sized to the screen. Ink is stored in that page space and rendered through a **single uniform scale**; the old separate x/y scales were what distorted strokes when the window ratio changed. Pre-A4 rows are converted on read (uniform, centred, so shapes survive) and only rewritten on the next save. The tool palette is a floating panel that snaps to an edge, and it deliberately carries **no transient text**: the save indicator sits in a fixed-width slot because a status that rendered nothing when idle reflowed the toolbar on every autosave.
- **Pictures on a paper page** (`paper_page_images`, `src/lib/paperImages.ts`, `PaperImageLayer.tsx`) — pasted from the clipboard or picked from a file, placed in the same A4 page space as strokes. Three things to know: the picture is **drawn by the canvas** (beneath the ink, so it lands in the snapshot) but **interacted with through a DOM overlay** that only mounts in select mode, which is what keeps handles at 44px and leaves the drawing pointer logic alone; rotation is 90° steps plus a mirror flag, applied about the image's own centre, and resizing scales about that centre so a handle stays under the finger at any angle; and **a locked image is skipped by the hit test entirely** — writing over a photo must not be able to grab it — with the server refusing geometry writes to a locked row too, since an in-flight drag can land after the lock. The stored extension list is closed on purpose: the file is served from our own origin, so an `.svg` would be a script.
- **Calendar events** repeat `daily | weekly | monthly | yearly` (`backend/calendar_recurrence.py`, still pure and DB-free). Yearly **clamps** Feb 29 to Feb 28 in common years rather than skipping it, matching how `_add_months` already clamps the 31st — a birthday should appear every year. `all_day` is an explicit column, _not_ `time IS NULL`: rows predating the flag are merely untimed and must not be retroactively relabelled. Setting the flag clears any stored times, and `_SPLIT_COLUMNS` has to carry it or a "this and future" split drops it.
- **Settings** owns more than AI keys: STT/TTS backends and Whisper model/device, voice + in-app shortcuts, curated tags, fanfic site cookies, HF token (diarization), meeting echo-cancel, task nudges, prevent-sleep (a `systemd-inhibit` subprocess), and a GPU **VRAM budget** view (non-LLM baseline measured at startup; the LLM's share and the card total are read **live** from `nvidia-smi`, because with expert tensors split across GPU and RAM a model's footprint can't be derived from its file size — thresholds in `src/lib/vram.ts`)
- **DB path** defaults to `./data/lunaschal.db`; override with `DATABASE_URL` env var
- **JWT secret** defaults to a hardcoded dev string; set `JWT_SECRET` env var in production
- **Ports: production Flask is 5000, dev Flask is 5001**, Vite dev is 5173 and proxies `/api` to :5001 (`VITE_API_PROXY_TARGET` overrides the target for split-machine dev). They are split because `lunaschal.service` now runs production full-time on :5000, so a dev run must neither bind that port nor kill what is on it — `start.sh`/`start-server.sh` deliberately exclude :5000 from their stale-process sweep, and `main.py --dev` health-checks :5001 (probing :5000 would find production and report ready before the dev backend existed). Override with `LUNASCHAL_PORT` / `LUNASCHAL_DEV_PORT`. The Vite watcher must keep ignoring `data/**` — WAL files churn on every request and previously OOM'd the dev server.
- **Production runs headless** (`main.py --headless` via `ops/run-prod.sh`): Flask in the foreground, no PyWebView. The windowed path exits 0 when its window closes, which under `Restart=on-failure` read as a clean shutdown and silently took the LAN server down. The unit is now `Restart=always`. Use `ops/open-window.sh` (or any browser) to open the UI as a _client_ — closing it stops nothing.
- **Network mode**: set `NETWORK_MODE=1` and `LUNASCHAL_PASSWORD=...` to bind `0.0.0.0` and enforce auth for LAN access

A Mermaid diagram of the module structure lives in `docs/architecture.md`.

## STT (Speech-to-Text)

STT/TTS is embedded directly in the Flask backend (`backend/routes/stt.py`). STT has three backends — `parakeet` (NVIDIA Parakeet TDT via onnx-asr, CPU-only, English, 0 VRAM; **the default**), `local` (openai-whisper, GPU/CPU), or `openai` (cloud). Parakeet is the default and Whisper defaults to CPU because llama-server holds most of the 8 GB card for as long as it runs — unlike Ollama, which released VRAM after `keep_alive` — so a CUDA Whisper alongside it means an OOM for whichever loads second. A diarized meeting (pyannote) needs real VRAM and currently requires unloading the model first (`POST /models/unload` on the router). TTS has two — local (kokoro-onnx) or OpenAI API. The Parakeet path decodes any input (incl. the browser's webm) to 16 kHz mono via ffmpeg before handing the waveform to `onnx-asr`.

```bash
# --- Local setup (GPU machine) ---
bash stt/setup.sh           # installs openai-whisper, kokoro-onnx, openwakeword

# --- API setup (low-power machine) ---
bash stt/setup.sh --api     # installs only openai client, skips local models
export OPENAI_API_KEY=sk-...
export STT_BACKEND=openai
export TTS_BACKEND=openai

# Terminal 1 — Flask app (handles STT/TTS routes)
npm run dev

# Terminal 2 — global voice input listener (keyboard shortcuts + audio capture)
./stt/run_listener.sh       # or: npm run stt

# Terminal 3 (optional) — morning check-in daemon
./stt/run_morning_checkin.sh

# Test morning check-in immediately (skips wake detection)
./stt/run_morning_checkin.sh --now
```

Shortcuts:

- **F1** (`STT_PASTE_KEY`) — record → transcribe → paste text at cursor via `wtype`
- **Right Alt** (`STT_VOICE_KEY`) — record → transcribe → AI chat (Lunaschal `/api/chat/stream`) → TTS reply spoken aloud
- (`STT_JOURNAL_KEY`) — record → transcribe → save as journal entry

All three shortcuts are rebindable in Settings → Voice Shortcuts (stored in the `settings` table; env vars are fallbacks). The listener also runs the task-nudge loop (see Tasks above). Every transcription is logged to the `transcriptions` table; `POST /api/transcribe/correct` re-runs a transcript through the LLM for cleanup.

The Flask backend handles `POST /api/transcribe` and `POST /api/tts` directly (no separate port 8765 service). The Whisper model loads lazily on the first transcription request. `stt/service.py` still exists as a standalone FastAPI server but is no longer used by default.

**Local TTS**: Kokoro-ONNX (~80 MB model cached to `~/.cache/lunaschal/tts/` on first run). **API TTS**: OpenAI (`tts-1`, voice configurable via `OPENAI_TTS_VOICE`, default `nova`). Voice assistant conversation history is kept in-memory for the lifetime of the listener process. `LUNASCHAL_URL` env var overrides the chat server URL (default: `http://127.0.0.1:5000`).

STT/TTS env vars summary:

| Var                | Default                     | Notes                                                                                                                     |
| ------------------ | --------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `STT_BACKEND`      | `parakeet`                  | `parakeet`, `local`, or `openai`. Defaults to CPU-only Parakeet — see above                                               |
| `TTS_BACKEND`      | `local`                     | `local` or `openai`                                                                                                       |
| `PARAKEET_MODEL`   | `nemo-parakeet-tdt-0.6b-v2` | onnx-asr model id when `STT_BACKEND=parakeet`                                                                             |
| `OPENAI_API_KEY`   | —                           | Required for openai backends                                                                                              |
| `OPENAI_TTS_VOICE` | `nova`                      | alloy / echo / fable / onyx / nova / shimmer                                                                              |
| `WHISPER_MODEL`    | `turbo`                     | Local STT only (tiny/base/small/medium/large/large-v2/large-v3/turbo)                                                     |
| `WHISPER_DEVICE`   | `cpu`                       | Local STT only (`cuda` or `cpu`). Defaults to CPU because llama-server holds most of the 8 GB card for as long as it runs |
| `STT_LISTENER`     | —                           | Set to `1` to auto-start the voice listener as a subprocess of Flask                                                      |

### Morning Check-in (`stt/morning_checkin.py`)

Daemon that monitors for wake-from-sleep events via a time-jump trick (sleeps 10 s; if the wall clock advanced >30 s, the system was suspended). When the machine wakes between `MORNING_START_HOUR` (default 8) and `MORNING_END_HOUR` (default 11), it starts a voice conversation that helps the user rubber-duck their plans for the day. A flag file in `$XDG_RUNTIME_DIR` prevents duplicate check-ins within the same calendar day.

Env vars: `STT_URL`, `LUNASCHAL_URL`, `MORNING_START_HOUR`, `MORNING_END_HOUR`.
