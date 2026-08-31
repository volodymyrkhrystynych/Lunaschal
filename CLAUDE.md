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
- **Every backend test gets an isolated database, whether it asks for one or not** — `conftest.py`'s autouse `isolated_db` copies a session-built schema template per test. `get_db()` opens the configured path lazily and never runs the schema, so before that fixture a test with no `client` opened the developer's real `./data/lunaschal.db` and silently depended on whatever was in it. The template is built once (`init_db()` is ~150 ms; 1,500 times is four minutes).
- That per-test DB copy (~1MB) lives in `tmp_path`, and pytest's default retention keeps every test's `tmp_path` alive for the whole session — across ~1,800 tests that's enough on its own to fill a quota-limited tmp partition. `pytest.ini` sets `tmp_path_retention_policy = failed` so a passing test's `tmp_path` is freed the moment that test ends (pytest still keeps failing tests' dirs around to debug), and `scripts/test-backend.sh` (what `npm run test:backend` runs) additionally splits the suite into batches, wiping pytest's tmp root before each one — so a full run's peak tmp usage stays bounded even when a batch has real failures.
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
npm run test:backend     # scripts/test-backend.sh: runs backend/tests in batches
                         # (pytest directly for a single file: .venv/bin/pytest backend/tests/test_foo.py)
npm run test             # vitest run (src/**/*.{test,spec}.{ts,tsx})
npm run test:all         # both suites
npm run test:watch       # vitest in watch mode
```

## Architecture

Lunaschal is a single-user personal life-management desktop app with AI integration. Recipes are no longer a top-level view — they live inside Food (`src/components/Food/RecipeList.tsx`) over the `cookbook` blueprint. Runs as a native desktop window via PyWebView, or as a web app on the LAN in network mode.

### Stack

- **AI**: local inference only, via **llama.cpp's `llama-server` in router mode** (`llama/presets.ini`, started by `llama/start-llama.sh` or the systemd unit beside it). It speaks the OpenAI API, so the whole `backend/ai/` layer goes through the `openai` SDK pointed at `http://localhost:8080` — one code path, no native-endpoint shim. The model is **Qwen3.6 35B A3B** (MoE, 35B total / 3B active), served with its routed expert tensors in system RAM and everything else in VRAM; that placement is the single biggest performance decision in the project, and [docs/learnings/moe-expert-placement.md](docs/learnings/moe-expert-placement.md) explains why. It replaced Gemma 4 26B A4B for **context**: only 10 of its 40 layers keep a cache that grows, and those have 2 KV heads apiece, so the same card holds 190k tokens instead of 90k — derivation in [docs/learnings/qwen36-context-budget.md](docs/learnings/qwen36-context-budget.md). Everything non-text (photo captions, non-speech audio description) goes to a **separate CPU-only any-to-any Gemma 4 12B**, `[gemma4-12b-omni]`, so it never competes for the card
- `drizzle.config.ts` is vestigial (points at a `server/db/schema.ts` that no longer exists) — the schema source of truth is `backend/db/schema.sql`

### Entry Points

- **`main.py`** — PyWebView desktop launcher. Starts Flask in a daemon thread, waits for `/api/health`, then opens the window. Pass `--dev` to point the window at the Vite dev server instead of the built `dist/` (health-checked on `:5001`, the dev port). **`--headless` skips the window entirely** and runs Flask in the foreground — that's the production server, and `webview`/Qt are imported inside `_start_window()` so this path needs no display. `--server-url` opens a window against an already-running server without starting one.
- **`backend/app.py`** — Flask app factory (`create_app`). Runs DB init, registers all blueprints, mounts auth middleware, serves the built `dist/` in production, restores the sleep inhibitor, snapshots baseline GPU VRAM for the Settings VRAM budget, and (with `STT_LISTENER=1`) spawns the voice listener as a subprocess.

### Backend Structure (`backend/`)

Feature-logic packages (kept out of the route files so they can be unit-tested):

- `backend/learning/` — FSRS scheduling adapter (`scheduler.py`), embedding dedup (`dedup.py`)
- `backend/fanfic/` — XenForo parsing/download pipeline, epub/docx import, HTML sanitizing, file storage
- `backend/meetings/` — ffmpeg recording, resumable Whisper pipeline, transcript merging, file storage
- `backend/newspapers/` — frontpages.com scraper, sync, file storage
- `backend/lifestyle/` — the four activity types and per-day heatmap collapse (`activity.py`), exercise-name canonicalization (`exercises.py`), selfie file storage
- `backend/journal/` — file storage for journal audio/photo attachments (`storage.py`)
- `backend/food/` — food-photo storage and EXIF capture-date/GPS extraction (`exif.py`)
- `backend/paper/` — file storage for handwritten page snapshots (`storage.py`)
- `backend/research/` — the Ideas agent: repo-scoped **code tools** (`code.py` — ripgrep/read/list, plus a graphify lookup when the repo has a graph), deterministic repo extraction (`repo_facts.py` for this app's shape, `repo_scan.py` for any checkout), SSRF-guarded web tools (`web.py`), the per-repo copy-on-write wiki, the sync tool loop, the research worker, the nightly code-wiki pass (`code_wiki.py`) and the evidence-backed assessment. Design record: [docs/ideas-tab.md](docs/ideas-tab.md)
- `backend/repos/` — repositories the Ideas agent reads, registered by git URL and cloned into `./data/repos/<slug>/`. `git.py`'s `assert_clone_url` is a **different** guard from `web.py`'s: there the model picks URLs, here the user does, so the threat is git's own transports (`ext::` runs an arbitrary command by design). `graph.py` wraps graphify, which builds a graph from nothing with `update` and needs no API key
- `backend/jobs/` — job applications: the master profile, feed triage (`triage.py`'s pure title gate → `ai/job_triage.py`'s one judge-and-condense call → `triager.py`'s worker), bounded-schema resume tailoring, the Answer Kit, email linkage over the existing `job_application` classifier, resume retention, discovery (`sources/` board adapters → `sync.py` → the phone's triage feed → `queue.py`'s background resume worker), and the routes the browser extension applies through
- `backend/delegate/` — the Chat tab's delegate: the proposal toolbox (`tools.py`), the loop that drives it (`agent.py`), and the decide-delegate-answer glue behind `/api/chat/stream` (`chat.py`). See Chat delegate below
- `backend/chat/` — file storage for chat photo attachments (`storage.py`) and the helper that turns their readings into text the chat model can see (`context.py`)
- `backend/email/` — Gmail/Outlook/IMAP sync, sanitizing, and `refetch.py`, which re-pulls message bodies stored before `emails.body_html` existed. That column defaults to empty, so pre-existing mail has a plain-text body and nothing else — for Indeed's application confirmations the HTML is the only place the employer's name ever was. Serial and rate-clamped; see [docs/application-backfill.md](docs/application-backfill.md)
- `backend/memory.py` — the one standing document about the user; read into every chat system prompt, copy-on-write, and written only from Settings → Memory
- `backend/observations.py` — the assistant's _own_ note queue beside that document, written instantly by the chat delegate's `remember` tool and capped rather than unbounded. Deliberately a separate store: `remember` was removed once for editing the user's document unasked, and what makes it safe to have back is that it cannot reach it. See `backend/delegate/CLAUDE.md`
- `backend/lifewiki/` — the life wiki: read-only tools over the user's own record (`tools.py`'s `LifeTools`: `search_conversations`, `search_journal`, `read_day`) on the chat delegate's decision turn, plus the nightly synthesis pass that writes `wiki_articles` rows with `kind='life'` (`digest.py` gathers a window from all six sources with a citation on every line, `facts.py` is the `life_facts` store, `job.py` runs extract → reconcile → render). **The prose is derived from the facts and never revised into itself** — an LLM rewriting its own prose nightly accumulates drift it cannot detect, so the Nth render reads N facts rather than N−1 renders, every fact cites the row it came from, and `rebuild_article` re-derives from those rows. Runs inside the briefing thread, before `run_briefing()`, on a wall-clock budget
- `backend/imaging.py` — HEIC→JPEG transcoding at the upload boundary, shared by the food log and chat photos (it also registers Pillow's HEIF opener)
- `backend/geo.py` — the one latitude/longitude validator (`parse_coord`, `coord_pair`); a lone coordinate is rejected rather than stored, since half a location is a row that looks located and isn't
- `backend/tags.py` — shared normalization for JSON-array tag columns (use it, don't grow per-feature rules)

The chat blueprint exposes a streaming SSE endpoint at `POST /api/chat/stream` using Flask's `Response(stream_with_context(...))`.

**There is one tool loop, in `backend/research/agent.py`**, parameterized by `tools=` and `dispatch=`. The Ideas agent and the chat delegate both drive it; a third copy is how the retired `backend/websearch/agent.py` ended up without the `checkpoint()` and the `finish_reason` check that the original had. A new agent supplies a toolbox, not a loop.

Long-running work (fic downloads, curated-tag scans, meeting transcription, Ideas research) runs in daemon threads with an in-memory progress registry; anything that must survive a restart is checkpointed to the DB, and `connection.py` resets orphaned in-flight states (`downloading` fics, `recording`/`transcribing` meetings, `running` idea research) at startup.

There is no cron and no general scheduler: hand-rolled daemon loops start from `create_app()` (all skipped when `LUNASCHAL_NO_SCHEDULERS` is set, which the test suite does). The model-using ones are staggered so they never contend for the two llama slots: `chat_title_scheduler` owns 02:00–03:00, `research/repo_scheduler` 03:00–05:00 (per repo: pull, rebuild its graphify graph, rescan, write a few code-wiki module notes), `briefing_scheduler` 05:00–07:00 (**two jobs in order in one thread**: the life-wiki pass, then the briefing that reads it — see `backend/briefing_scheduler.py`), while `research/research_scheduler` runs in **no window at all**, deferring moment to moment through `backend/ai/priority.py` instead. `email_scheduler` and `email/images`' fetcher poll on their own intervals. `jobs/scheduler` mostly needs no slot — its linkage sweep is pure string matching and its board sync makes no model call — so both sweep every tick; only its resume-queue drain asks `priority`, and only its daily file purge keeps to a window (07:00–08:00, after the other four).

### Database Layer (`backend/db/`)

- `schema.sql` — raw SQL `CREATE TABLE IF NOT EXISTS` statements; all IDs are ULIDs; timestamps are unix ints (converted to ISO strings by `row_to_dict`, which also camelCases column names — see `TIMESTAMP_COLS`)
- `connection.py` — opens a single WAL-mode SQLite connection (`get_db()`), runs `schema.sql` on startup, then a long list of `_ensure_*` helpers: **migrations are idempotent ALTER TABLEs guarded by `PRAGMA table_info` checks** — follow that pattern for new columns
- Six FTS5 virtual tables maintained by SQL triggers: `journal_fts`, `recipes_fts`, `fic_chapters_fts`, `wiki_fts`, `emails_fts`, `messages_fts`. `messages_fts` is the newest and the reason the chat agent can reach a past conversation at all — the client only ever sends the segment since the last "New chat". All of them build their MATCH expression through `connection.py`'s one `fts_match_query`, which drops punctuation: three of the four older helpers split on whitespace instead, so a query containing a double quote raised OperationalError rather than returning nothing
- Binary/media files live next to the DB under `./data/`: `fanfic/<fic_id>/` (images, PDFs), `meetings/<id>/` (WAV tracks), `newspapers/`, `journal/<attachment_id>/` (entry audio + photos), `lifestyle/<id>/` (daily selfies), `food/<id>/` (meal photos), `chat/<conversation_id>/` (chat photos), `paper/<page_id>/` (page snapshots + pasted pictures), `jobs/<application_id>/` (rendered resumes, deleted on the retention sweep), `repos/<slug>/` (git checkouts the Ideas agent reads, each with its own graphify graph inside — **excluded from `ops/backup.sh`**, since they are one `git clone` away), plus `shortcuts.json` (in-app key bindings). Roots overridable via `FANFIC_ROOT` / `MEETINGS_ROOT` / `NEWSPAPERS_ROOT` / `JOURNAL_ROOT` / `LIFESTYLE_ROOT` / `FOOD_ROOT` / `CHAT_ROOT` / `PAPER_ROOT` / `JOBS_ROOT` / `REPOS_ROOT` / `SHORTCUTS_PATH`.

### AI Layer (`backend/ai/`)

- `provider.py` — resolves the llama-server URL and model alias from DB settings (`llama_url` / `llama_model`, defaulting to `http://localhost:8080` and `qwen36`). Model names are **router aliases** — section names in `llama/presets.ini` — not file names or Ollama tags
- `llm.py` — shared generation helpers over llama-server's OpenAI API: `chat_json`, `chat_text`, `chat_messages`, `chat_stream_deltas`, `chat_stream_events`, `chat_with_tools`. Three things to know: **`chat_json` takes a `schema=`** (JSON Schema) which llama-server compiles to a GBNF grammar — every call site passes one, and closed vocabularies like the journal tag list are enforced by the grammar rather than requested in the prompt; **thinking is a boolean, not a level** (`enable_thinking` via `chat_template_kwargs`), sent explicitly because the chat template defaults it _on_ (true of Gemma 4 and of Qwen3.6), and because a template that doesn't know the kwarg ignores it — which is what makes the setting survive a model swap; and **reasoning arrives two different ways** — as a `reasoning_content` delta field or inline in a `<think>` block — depending on the server's flags, not the request, so `chat_stream_events` handles both and labels them `('thinking', …)` apart from `('content', …)`. The inline case is tracked with a running flag rather than a regex, because the tags arrive split across chunks. `chat_stream_deltas` is now a filter over it, so every existing caller still gets the answer alone. There is deliberately **no per-request context window** — llama-server fixes it at load time
- `chat.py` — system-prompt assembly (journal + schedule context, time stamping) and `chat_stream`, still used by the STT transcript-cleanup route
- `chat_title.py` — nightly conversation titling
- `embeddings.py` — text embeddings for Learning answer-dedup, via the `embed` router alias. Still **nomic-embed-text-v1.5** on purpose: the float32 vectors already stored on `learning_cards` are compared by cosine similarity, so a different embedding model would silently invalidate every stored vector
- `journal.py` — entry polish/metadata (tags constrained to the closed `JOURNAL_TAGS` vocabulary by schema enum); `polish_journal_entry`'s `context` also carries the standing memory document alongside attachment descriptions, so a misheard name gets fixed against both — this is where Chat dictation's now-removed correction pass moved; `classify_entry_for_tag(content, tag_name) -> bool` for the curated-tag background scan
- `idea_polish.py` — `polish_idea`, the same memory-document correction as Journal's but lighter (no paragraph reformatting — ideas stay one short block); fires once, in the background, right after `POST /api/ideas/voice` (see backend/research/CLAUDE.md)
- `images.py` — `describe_image(path, *, system, prompt)` is the **only** call in the app that sends an image anywhere; `caption_image` (journal prose) and `read_chat_photo` (chat, quotes legible text verbatim) are its two callers
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
- `src/components/` — one file (or subdirectory) per view: `Chat/` (`ChatPanel` + the shared `AgentSteps` / `ReasoningBlock` / `ThinkingLabel`), `ChatNav`, `Tasks/` (no longer a view — `TasksSection` mounts inside Lifestyle), `Journal`, `Meetings`, `Writing/`, `Calendar`, `Learning/`, `Cookbook`, `Fanfic/` (library + folders + reader), `Newspapers`, `Jobs/` (pipeline + profile + Answer Kit), `Editor/` (file editor + STT panel), `Settings` (+ `CuratedTagsSection`, `ShortcutSettings`)
- **Adding a top-level view takes four edits that must agree**: `VIEWS` in `src/lib/viewPersistence.ts`, the `switch` in `App.tsx`, `View` + `navItems` in `Sidebar.tsx`, and `AppView` + `VIEW_ORDER` in `src/shortcuts/ShortcutProvider.tsx` (that order must match `navItems`, since nav.up/down walks it)
- `Chat/AgentSteps.tsx` is shared with the Ideas discussion, and `src/lib/agentSteps.ts` holds the one `stepLabel` both use — each had grown its own copy of the `<details>` block and the labeller. An agent trace renders **collapsed even while streaming**: a growing list of steps used to push the reply down the page as it was being read
- `src/hooks/api.ts` — typed REST client (`api.*` namespaces) using plain `fetch`; no tRPC
- `src/lib/` — pure logic extracted for node-environment tests (todo sorting, tag parsing, journal feed grouping, font-size steps, fanfic helpers, VRAM thresholds…)
- `src/shortcuts/` — the in-app keyboard system (see below)
- `@` path alias resolves to `./src/`
- CSS custom properties (e.g. `var(--color-bg)`) are used for theming throughout

### In-app keyboard shortcuts (`src/shortcuts/`)

Keyboard-first, single-key navigation (the Pocket 2 has no usable mouse): WASD-style `nav.up/down/out/in`, `N` new item, `B` sidebar, plus per-view actions — all `ActionId`s and defaults in `keymap.ts`. Bindings are user-editable in Settings → Shortcuts and persisted server-side (`GET/PUT /api/shortcuts` → `data/shortcuts.json`). `ShortcutProvider` owns the global keydown listener (skipping editable targets), view cycling, and numbered **shortcut scopes** for list navigation — a scope number must be registered only once per mounted tree (last registration silently wins; the Writing nav is the canonical single-scope-2 owner). The number row is deliberately unbound for tabs — it belongs to Learning review ratings. This browser-side keymap (KeyboardEvent.code combos) is separate from the evdev key names the OS-level STT listener uses; `ShortcutProvider` maps evdev combos from settings so the listener's keys can be shown/avoided.

### Feature modules

#### Chat delegate — see [`backend/delegate/CLAUDE.md`](backend/delegate/CLAUDE.md) for the delegate/proposal toolbox, chat photos, Memory, and dictation.

#### Learning — see [`backend/learning/CLAUDE.md`](backend/learning/CLAUDE.md).

#### Practice — see [`backend/practice/CLAUDE.md`](backend/practice/CLAUDE.md).

#### Writing — see [`src/components/Writing/CLAUDE.md`](src/components/Writing/CLAUDE.md).

#### Life wiki — see [`backend/lifewiki/CLAUDE.md`](backend/lifewiki/CLAUDE.md) for the nightly synthesis pass, why the prose is rendered from facts rather than revised, and the drift invariants that follow from it.

#### Ideas — see [`backend/research/CLAUDE.md`](backend/research/CLAUDE.md) for registered repositories and their clones, the code toolbox, the repo-context agent, the per-repo wiki and its nightly pass, evidence-backed assessment, and discussion/plans.

#### Jobs — see [`backend/jobs/CLAUDE.md`](backend/jobs/CLAUDE.md) for the profile, the anti-fabrication tailoring bounds, the Answer Kit, ATS-aware email linkage, resume retention, and the browser extension's routes. Design record: [docs/jobs-tab.md](docs/jobs-tab.md)

#### Browser extension (`extension/`) — the desktop half of applying: an unpacked MV3 extension that fills real ATS forms from the profile, attaches the tailored resume and records what was answered. **No build step** — it is loaded unpacked, so the source is the extension, and its pure modules (`lib/fields.js`, `lib/filename.js`) are plain `.js` that Vitest imports directly (`vite.config.ts`'s `include` covers `extension/**/*.test.js`). See [`extension/README.md`](extension/README.md); the three constraints that shaped it are that content scripts cannot call the backend (CORS — everything goes through the service worker), that it holds **no host permission for job sites** (injected on a gesture via `activeTab`, which is also what makes embedded boards work), and that React forms ignore a plain `el.value =`.

#### Fanfic library — see [`backend/fanfic/CLAUDE.md`](backend/fanfic/CLAUDE.md).

#### Meetings — see [`backend/meetings/CLAUDE.md`](backend/meetings/CLAUDE.md).

#### Cookbook (`backend/routes/cookbook.py`, `backend/ai/recipes.py`, `src/components/Cookbook.tsx`)

Recipe collection. Paste text or a URL — the page is fetched and stripped, then `parse_recipe` extracts title/markdown-content/tags via LLM JSON mode. FTS search (`recipes_fts`), tag filtering.

#### Tasks & todos (`backend/routes/tasks.py`, `src/components/Tasks/`)

Two lists: **daily tasks** (max 4, per-day completions in `daily_task_completions`, reset each day) and one-off **todos** (`todo` / `archive` — the third list, `chores`, was folded into `todo`). **There is no Tasks tab**: `TasksSection` renders inside the Lifestyle view, directly under the activity heatmap, and owns shortcut scopes 1 and 2 there. The STT listener runs a **task-nudge loop**: on an interval (Settings → nudges, default 45 min, waking-hours window) it picks a pending daily task and starts a short spoken check-in conversation about it.

#### Lifestyle — see [`backend/lifestyle/CLAUDE.md`](backend/lifestyle/CLAUDE.md).

#### Newspapers (`backend/routes/newspapers.py`, `backend/newspapers/`)

Archives daily front pages (Toronto Star, NYT) from frontpages.com. The scraper decodes the base64-inlined image URL (the `og:image` is a decoy) and **dates editions by the date embedded in the image URL, not the local clock** — the site can serve yesterday's edition past midnight. `POST /api/newspapers/sync` is idempotent per (paper, date).

#### Transcriptions (`backend/routes/transcriptions.py`)

Append-only log of everything the STT pipeline transcribed (source/app/detail). The Journal feed can interleave them between entries (`src/lib/journalFeed.ts`; transcriptions are visible but not selectable).

### Key Behaviors

- **Curated tags** — user-defined tags managed in Settings → Tags tab. Each new tag triggers a background daemon thread that calls `classify_entry_for_tag` per journal entry and writes matches to `journal_entry_curated_tags`. Progress tracked in-memory (`_scan_progress` dict in `curated_tags.py`); the list endpoint merges it in. Tags appear as filter pill buttons in the Journal view; entries display curated tags (`#name`, neutral style) separately from freeform AI tags (accent color).
- **Journal entries** keep `raw_content` (as typed/spoken) alongside AI-polished `content`; polish and metadata generation run as background threads after save. The Journal feed also interleaves fic-reading commentary via `journal_entry_fic_refs`. `polish_journal_entry` **raises `PolishUnavailable` rather than falling back to the raw text** — the two used to be indistinguishable, so an offline llama-server overwrote a polished entry with its transcript and returned 200; the manual Polish route now answers 503 and leaves `content` alone.
- **Journal attachments** — see [`backend/journal/CLAUDE.md`](backend/journal/CLAUDE.md).
- **Paper pages and pictures on a page** — see [`backend/paper/CLAUDE.md`](backend/paper/CLAUDE.md).
- **Calendar events carry two independent tag columns**: `tags` is free text the user writes in the create/edit form (comma-separated input → `parseTagsInput` → `backend/tags.py`'s `tags_json`, so "Work" and "work " are one tag here exactly as everywhere else, and an emptied list stores NULL rather than `'[]'`), while `category_tags` is what `backend/ai/calendar.py` assigns. They are deliberately separate so a user-typed pill can never collide with a classifier result. `GET /api/calendar/tags` feeds the filter pill row from the whole table, not the month on screen — a filter offering only the tags of what you are already looking at cannot be used to find the rest.
- **The mobile day view draws the 4am day, so it spans two calendar dates**: `DayView` fetches both `date` and the date after it, shows the first from 04:00 and the second up to 04:00, and positions everything in _offset minutes_ down the timeline rather than wall minutes (`src/lib/calendarDayLayout.ts` holds both spaces and the conversions). Two consequences worth knowing: a drag past the midnight rule rewrites the event's `date`, not just its `time` — sent only when the date really changed; and `sleepBands` is anchored at 4am too, so the night a day ends with is one contiguous band at the bottom instead of being split across two views.
- **Calendar events** repeat `daily | weekly | monthly | yearly` (`backend/calendar_recurrence.py`, still pure and DB-free). Yearly **clamps** Feb 29 to Feb 28 in common years rather than skipping it, matching how `_add_months` already clamps the 31st — a birthday should appear every year. `all_day` is an explicit column, _not_ `time IS NULL`: rows predating the flag are merely untimed and must not be retroactively relabelled. Setting the flag clears any stored times, and `_SPLIT_COLUMNS` has to carry it or a "this and future" split drops it.
- **Settings groups are collapsed by default** (`Settings/CollapsibleSection.tsx`) — the General tab holds fifteen of them. A section can pass `autoExpand` to open itself once when something is wrong; only Backup uses it, and only for a genuinely broken backup, since collapsing by default is worthless if every group finds a reason to reopen.
- **Settings → Backup** watches the nightly `ops/backup.sh` job by the age of the newest snapshot **on the drive**, not by the job's exit code. The script skips-rather-than-fails on a missing destination, so a permanently-dead backup exits 0 exactly like a one-night outage — which is how a stale `BACKUP_HDD_PATH` hid nineteen days of no backups behind `Result=success`. Classification is pure and testable in `backend/ops/backup_status.py`; `backend/routes/backup.py` adds the filesystem and `systemctl --user` reads plus a manual trigger. **Settings is the source of truth for the destination and retention** (`settings.backup_path` / `backup_retention_days`); `ops/backup.sh` asks the DB via `python -m backend.ops.backup config --get`, so the path the panel shows is the path the job uses. `ops/backup.env` keeps only the tablet destination and acts as a fallback for a DB that has never had a path — `_ensure_backup_settings` seeds the column from it once, because shipping an empty column would silently unconfigure a working backup. The folder picker browses server-side (`GET /api/backup/browse`) rather than using a native dialog or `<input webkitdirectory>`: the value is a server path for rsync, and it has to work in network mode as well as the desktop window. `readonly` (a genuinely `ST_RDONLY` mount) and `permissions` (a read-write mount this user cannot write to) are separate states because `os.access()` cannot tell them apart and their fixes are unrelated — fsck versus missing `uid=`/`gid=` in fstab, which is what exFAT needs since it stores no POSIX ownership of its own.
- **Settings → Logs** is a read-only viewer for four fixed `systemd --user` journals (`lunaschal`, `-llama`, `-deploy`, `-backup`) so the server's state is visible from the phone or the Pocket 2 without SSH. `backend/routes/logs.py` shells out to `journalctl -o json`; `backend/ops/journal_logs.py` is the pure parser + argv builder (unit from a fixed allowlist, `lines`/`priority` coerced, `since` from a preset map — request input never reaches the command line). Degrades to `available: false` under a bare shell with no user bus, same as the Backup panel. Severity/search/hide-requests filtering is client-side over what came back (`src/lib/serverLogs.ts`).
- **Settings** owns more than AI keys: STT/TTS backends and Whisper model/device, voice + in-app shortcuts, curated tags, fanfic site cookies, HF token (diarization), meeting echo-cancel, task nudges, the three wall-clock timeouts (`backend/delegate/limits.py` — a whole reply, a delegate search, a deep-research pass), prevent-sleep (a `systemd-inhibit` subprocess), and a GPU **VRAM budget** view (non-LLM baseline measured at startup; the LLM's share and the card total are read **live** from `nvidia-smi`, because with expert tensors split across GPU and RAM a model's footprint can't be derived from its file size — thresholds in `src/lib/vram.ts`)
- **DB path** defaults to `./data/lunaschal.db`; override with `DATABASE_URL` env var
- **JWT secret** defaults to a hardcoded dev string; set `JWT_SECRET` env var in production
- **Ports: production Flask is 5000, dev Flask is 5001**, Vite dev is 5173 and proxies `/api` to :5001 (`VITE_API_PROXY_TARGET` overrides the target for split-machine dev). They are split because `lunaschal.service` now runs production full-time on :5000, so a dev run must neither bind that port nor kill what is on it — `start.sh`/`start-server.sh` deliberately exclude :5000 from their stale-process sweep, and `main.py --dev` health-checks :5001 (probing :5000 would find production and report ready before the dev backend existed). Override with `LUNASCHAL_PORT` / `LUNASCHAL_DEV_PORT`. The Vite watcher must keep ignoring `data/**` — WAL files churn on every request and previously OOM'd the dev server.
- **Production runs headless** (`main.py --headless` via `ops/run-prod.sh`): Flask in the foreground, no PyWebView. The windowed path exits 0 when its window closes, which under `Restart=on-failure` read as a clean shutdown and silently took the LAN server down. The unit is now `Restart=always`. Use `ops/open-window.sh` (or any browser) to open the UI as a _client_ — closing it stops nothing.
- **Network mode**: set `NETWORK_MODE=1` and `LUNASCHAL_PASSWORD=...` to bind `0.0.0.0` and enforce auth for LAN access

A Mermaid diagram of the module structure lives in `docs/architecture.md`.

## STT (Speech-to-Text)

Voice input/output setup, backends, shortcuts, env vars, and the morning check-in daemon — see [`stt/CLAUDE.md`](stt/CLAUDE.md).
