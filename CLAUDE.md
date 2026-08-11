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

Lunaschal is a single-user personal life-management desktop app with AI integration. Views (in sidebar order): spaced-repetition learning, code-syntax practice drills, AI chat, daily tasks + todos, journal, notebook, meeting recorder/transcriber, creative-writing workspace, ideas + research agent, calendar, food log (+ recipes), lifestyle (workouts/heatmap/chores/selfie/calories), fanfic library/reader, newspaper front pages, handwritten paper, file editor, settings. Recipes are no longer a top-level view — they live inside Food (`src/components/Food/RecipeList.tsx`) over the `cookbook` blueprint. Runs as a native desktop window via PyWebView, or as a web app on the LAN in network mode.

### Stack

- **Desktop shell**: PyWebView — `main.py` starts Flask in a background thread then opens a `webview.create_window`
- **Frontend**: React 19 + Vite + Tailwind CSS v4 — in `src/`
- **Backend**: Flask (Python) — in `backend/`
- **API layer**: REST JSON + React Query; typed client in `src/hooks/api.ts` (one `api.*` namespace per feature)
- **Database**: SQLite via Python's built-in `sqlite3`; stored at `./data/lunaschal.db`
- **AI**: local inference only, via **llama.cpp's `llama-server` in router mode** (`llama/presets.ini`, started by `llama/start-llama.sh` or the systemd unit beside it). It speaks the OpenAI API, so the whole `backend/ai/` layer goes through the `openai` SDK pointed at `http://localhost:8080` — one code path, no native-endpoint shim. The model is **Qwen3.6 35B A3B** (MoE, 35B total / 3B active), served with its routed expert tensors in system RAM and everything else in VRAM; that placement is the single biggest performance decision in the project, and [docs/learnings/moe-expert-placement.md](docs/learnings/moe-expert-placement.md) explains why. It replaced Gemma 4 26B A4B for **context**: only 10 of its 40 layers keep a cache that grows, and those have 2 KV heads apiece, so the same card holds 190k tokens instead of 90k — derivation in [docs/learnings/qwen36-context-budget.md](docs/learnings/qwen36-context-budget.md). Everything non-text (photo captions, non-speech audio description) goes to a **separate CPU-only any-to-any Gemma 4 12B**, `[gemma4-12b-omni]`, so it never competes for the card
- `drizzle.config.ts` is vestigial (points at a `server/db/schema.ts` that no longer exists) — the schema source of truth is `backend/db/schema.sql`

### Entry Points

- **`main.py`** — PyWebView desktop launcher. Starts Flask in a daemon thread, waits for `/api/health`, then opens the window. Pass `--dev` to point the window at the Vite dev server instead of the built `dist/` (health-checked on `:5001`, the dev port). **`--headless` skips the window entirely** and runs Flask in the foreground — that's the production server, and `webview`/Qt are imported inside `_start_window()` so this path needs no display. `--server-url` opens a window against an already-running server without starting one.
- **`backend/app.py`** — Flask app factory (`create_app`). Runs DB init, registers all blueprints, mounts auth middleware, serves the built `dist/` in production, restores the sleep inhibitor, snapshots baseline GPU VRAM for the Settings VRAM budget, and (with `STT_LISTENER=1`) spawns the voice listener as a subprocess.

### Backend Structure (`backend/`)

Flask blueprints in `backend/routes/`: `auth`, `journal`, `calendar`, `learning`, `settings`, `chat`, `files`, `writing`, `stt`, `tasks`, `curated_tags`, `shortcuts`, `transcriptions`, `cookbook`, `food`, `fanfic`, `newspapers`, `meetings`, `notebook`, `paper`, `lifestyle`, `ideas`, `memory`.

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
- `backend/chat/` — file storage for chat photo attachments (`storage.py`) and the helper that turns their readings into text the chat model can see (`context.py`)
- `backend/memory.py` — the one standing document the assistant keeps about the user; read into every chat system prompt, copy-on-write
- `backend/imaging.py` — HEIC→JPEG transcoding at the upload boundary, shared by the food log and chat photos (it also registers Pillow's HEIF opener)
- `backend/tags.py` — shared normalization for JSON-array tag columns (use it, don't grow per-feature rules)

The chat blueprint exposes a streaming SSE endpoint at `POST /api/chat/stream` using Flask's `Response(stream_with_context(...))`.

**There is one tool loop, in `backend/research/agent.py`**, parameterized by `tools=` and `dispatch=`. The Ideas agent and the chat delegate both drive it; a third copy is how the retired `backend/websearch/agent.py` ended up without the `checkpoint()` and the `finish_reason` check that the original had. A new agent supplies a toolbox, not a loop.

Long-running work (fic downloads, curated-tag scans, meeting transcription, Ideas research) runs in daemon threads with an in-memory progress registry; anything that must survive a restart is checkpointed to the DB, and `connection.py` resets orphaned in-flight states (`downloading` fics, `recording`/`transcribing` meetings, `running` idea research) at startup.

There is no cron and no general scheduler: four hand-rolled daemon loops start from `create_app()` (all skipped when `LUNASCHAL_NO_SCHEDULERS` is set, which the test suite does). `chat_title_scheduler` owns 02:00–03:00, `research/repo_scheduler` 03:00–05:00, `briefing_scheduler` 05:00–07:00 — staggered so they never contend for the two llama slots — and `research/research_scheduler` runs in **no window at all**, deferring moment to moment through `backend/ai/priority.py` instead.

### Database Layer (`backend/db/`)

- `schema.sql` — raw SQL `CREATE TABLE IF NOT EXISTS` statements; all IDs are ULIDs; timestamps are unix ints (converted to ISO strings by `row_to_dict`, which also camelCases column names — see `TIMESTAMP_COLS`)
- `connection.py` — opens a single WAL-mode SQLite connection (`get_db()`), runs `schema.sql` on startup, then a long list of `_ensure_*` helpers: **migrations are idempotent ALTER TABLEs guarded by `PRAGMA table_info` checks** — follow that pattern for new columns
- Four FTS5 virtual tables maintained by SQL triggers: `journal_fts`, `recipes_fts`, `fic_chapters_fts`, `wiki_fts`
- Binary/media files live next to the DB under `./data/`: `fanfic/<fic_id>/` (images, PDFs), `meetings/<id>/` (WAV tracks), `newspapers/`, `journal/<attachment_id>/` (entry audio + photos), `lifestyle/<id>/` (daily selfies), `food/<id>/` (meal photos), `chat/<conversation_id>/` (chat photos), `paper/<page_id>/` (page snapshots + pasted pictures), plus `shortcuts.json` (in-app key bindings). Roots overridable via `FANFIC_ROOT` / `MEETINGS_ROOT` / `NEWSPAPERS_ROOT` / `JOURNAL_ROOT` / `LIFESTYLE_ROOT` / `FOOD_ROOT` / `CHAT_ROOT` / `PAPER_ROOT` / `SHORTCUTS_PATH`.

### AI Layer (`backend/ai/`)

- `provider.py` — resolves the llama-server URL and model alias from DB settings (`llama_url` / `llama_model`, defaulting to `http://localhost:8080` and `qwen36`). Model names are **router aliases** — section names in `llama/presets.ini` — not file names or Ollama tags
- `llm.py` — shared generation helpers over llama-server's OpenAI API: `chat_json`, `chat_text`, `chat_messages`, `chat_stream_deltas`, `chat_stream_events`, `chat_with_tools`. Three things to know: **`chat_json` takes a `schema=`** (JSON Schema) which llama-server compiles to a GBNF grammar — every call site passes one, and closed vocabularies like the journal tag list are enforced by the grammar rather than requested in the prompt; **thinking is a boolean, not a level** (`enable_thinking` via `chat_template_kwargs`), sent explicitly because the chat template defaults it _on_ (true of Gemma 4 and of Qwen3.6), and because a template that doesn't know the kwarg ignores it — which is what makes the setting survive a model swap; and **reasoning arrives two different ways** — as a `reasoning_content` delta field or inline in a `<think>` block — depending on the server's flags, not the request, so `chat_stream_events` handles both and labels them `('thinking', …)` apart from `('content', …)`. The inline case is tracked with a running flag rather than a regex, because the tags arrive split across chunks. `chat_stream_deltas` is now a filter over it, so every existing caller still gets the answer alone. There is deliberately **no per-request context window** — llama-server fixes it at load time
- `chat.py` — system-prompt assembly (journal + schedule context, time stamping) and `chat_stream`, still used by the STT transcript-cleanup route
- `chat_title.py` — nightly conversation titling
- `embeddings.py` — text embeddings for Learning answer-dedup, via the `embed` router alias. Still **nomic-embed-text-v1.5** on purpose: the float32 vectors already stored on `learning_cards` are compared by cosine similarity, so a different embedding model would silently invalidate every stored vector
- `journal.py` — entry polish/metadata (tags constrained to the closed `JOURNAL_TAGS` vocabulary by schema enum); `classify_entry_for_tag(content, tag_name) -> bool` for the curated-tag background scan
- `images.py` — `describe_image(path, *, system, prompt)` is the **only** call in the app that sends an image anywhere; `caption_image` (journal prose) and `read_chat_photo` (chat, quotes legible text verbatim) are its two callers
- `memory.py` — background rewrite of the standing memory document to an instruction; returns None rather than a fallback, because a failed revision must leave the document alone
- `transcript.py` — `correct_transcript(text, *, memory, photo_notes)`; fixes misheard words against a reference and returns the input unchanged when there is no reference to check against
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

The Chat tab is one conversation with one endpoint. The toolbox is split on one line: **tools that need conversation context run on the main chat's own turn; tools whose output is large hide behind `delegate({task})`.** Things to know:

- **It replaced a post-hoc classifier, and the failure mode is the point.** `backend/ai/classifier.py` ran _after_ the reply, gated by a hand-rolled `should_classify` heuristic, and turned every exception into a fake low-confidence `conversation` result — so a request that failed produced no reply, no error and no log line, indistinguishable from a message never meant as a request. The decision now happens before the reply, the model makes it, and a failure is a visible error on the stream.
- **The `propose_*` tools are on the main chat's turn, and they were not always.** They lived in the delegate's loop, which is handed one `task` string and cannot see the conversation — so "by Friday" and "it's urgent" survived only if the main model remembered to restate them, and a staged to-do routinely arrived bare. They are small schemas returning one short string, so the main chat can afford them, and it already has the conversation, the schedule and `format_now_context()` to resolve "Friday" against. The delegate keeps `web_search` / `web_fetch` / `deep_research`, whose results are enormous: a page dump belongs in a summary, not in a transcript paid for on every later turn.
- **The delegate is research-only, and it now knows what day it is.** `agent._system_prompt()` appends `format_now_context()` per call (not at import — a process outliving midnight would keep saying yesterday). It never had a clock, so any date it produced was invented.
- **Proposals propose; they never write.** `propose_task` / `propose_calendar_event` / `propose_calorie_log` / `propose_food_log` / `propose_note_to_self` / `propose_flashcards` stage a payload and return "nothing has been saved yet" to the model, which is what stops the reply claiming it's done. They validate against the todos API's own rules (`backend/todo_recurrence.py`'s `parse_due_date` / `parse_priority` / `parse_repeat`, shared with `backend/routes/tasks.py`) so a bad value is refused where the model can read the reason, not at the click.
- **`remember` and `revise_memory` are the exception, and it is narrow.** They edit `backend/memory.py`'s standing document with no confirm card, because the case they exist for is the user correcting a misheard name mid-sentence and a click there costs more than it protects. What makes it safe is not the write being small but its being reversible — every change snapshots the previous document, and Settings → Memory shows the whole history. Their step events carry no `proposal` (the `ask_user` shape), so nothing about them can reach the card path, and `stepLabel` says "Remembered", never "Staged": the user has to know something _was_ written in order to go and unwrite it. See Memory below.
- **`propose_food_log` writes a meal, not a number.** `propose_calorie_log` stays for a bare "a coke, 140 cal"; a described meal goes to `_accept_food`, which writes a `food_entries` row (its `raw_content`/`notes` split is the same one `journal_entries` has), copies the message's photos into `food_media`, applies the photo's EXIF capture date as `created_at`, and writes `calorie_logs` too when a number was given. The two things the model **cannot** supply — the photo and the verbatim transcript — are resolved server-side at accept time from the user message this reply answered, so an edited card can't rewrite what was actually said. That lookup tie-breaks on the message ULID, not `created_at`: a whole exchange fits inside one second, and comparing timestamps alone picked up whatever was typed _after_ the reply.
- **Accept handlers take `(db, data, ctx)`.** `ctx` carries `messageId`, which is what lets a handler reach the message's photos and `raw_content` without either becoming an editable field on the card.
- **`ask_user` is the alternative to guessing, and it stages nothing.** Its event carries no `proposal`, so asking can never leave a card built on a guess. It fires when a detail was clearly implied but named too loosely to act on ("soon", "before the trip", an event with no workable date); it deliberately does _not_ fire when nothing was implied — "add buy milk" stages undated at neutral priority with no question. Exclusivity is per item, not per turn: staging one to-do while asking about another is normal, so the turn's other proposals are never dropped. The silent defaults it replaced were small lies — an event with no date used to be stamped with today's, and confirming that fiction was one click.
- **A proposal rides its own step event** (`event['proposal']`), so the shared loop needs to know nothing about proposals — it already collects events, and the caller filters. Proposals are never parsed back out of the model's prose, the same stance `research/agent.py` takes about recording sources from what was actually fetched.
- **A proposal is a durable row, not a live event.** When a run started via `backend/delegate/runs.py` finishes, every staged proposal except `note` (which drafts flashcards immediately with no confirm step — the draft _is_ the review) gets a stable id and `status: 'pending'` written into the assistant message's metadata. `POST /api/chat/proposals/<message_id>/<proposal_id>` (`{action: 'accept'|'dismiss'}`) is the only place a proposal ever leaves `'pending'` — same shape as `decide_briefing_todos`, and it's what makes a confirm card survive a reload or a dropped connection instead of living only in the browser's React state. Accepting dispatches by `kind` to a small per-kind writer (`_accept_calendar`/`_accept_calorie`/`_accept_food`/`_accept_task`/`_accept_flashcards` in `backend/routes/chat.py`) and stores its result back onto the proposal, so the resolved state renders from `metadata` alone on the next load.
- **The confirm card is a form, not a receipt.** It was read-only, so a due date one day out could only be dismissed and retyped in the Tasks view. Accepting posts the card's current values as `data`, which replaces the staged payload and goes through the same accept handler — the handlers are the validation boundary and nothing arriving there is trusted for having been proposed. A rejected edit answers 400 and leaves the card `'pending'`, because a card that failed validation is one the user still has to fix. The accepted `data` is stored back so a reload renders what was saved, not what was first suggested. `due` travels through the whole proposal layer as `YYYY-MM-DD` and becomes a timestamp only in `_accept_task` — at local noon, matching `src/lib/todos.ts`'s `dueInputToUnix`, or a date set in chat would land a day off from one set in the todo form.
- **Only the delegate's closing summary crosses back into the main conversation**, never its transcript — that compression _is_ the point of delegating. A truncated run doesn't get to pass off its half-sentence as that summary (`agent._summary`).
- **The decision turn is separate from the answer and its prose is discarded.** Tool turns can't be streamed (llama-server reconstructs `tool_calls` from a grammar; reassembling partial deltas drops arguments), so the turn that may carry a tool call is blocking, capped at `DECISION_MAX_TOKENS`, and told to emit nothing when nothing needs doing. The answer then streams off a prompt llama-server has already cached, so the restart costs generation, not prefill. It is **one round, not a loop** — proposals are one-shot and the delegate is itself a loop, so a second blocking turn would only double the dead air. It takes **every** tool call on the turn, not the first: a message can legitimately stage one thing and ask about another. The cap went 160 → 320 when the proposal schemas moved onto it, and 320 → 512 when `propose_food_log` joined (dish/place/notes/calories/rating/tags is the largest argument set on the turn, and a food message often stages that _and_ a `remember` in one breath), because a truncated tool call returns no `tool_calls` at all and is otherwise indistinguishable from "nothing to do". This is also why `revise_memory` takes an _instruction_ rather than the rewritten document: a 4,000-character argument would truncate every time.
- **A caller-supplied `systemPrompt` turns the whole toolbox off** (`tools_enabled=False`). The voice listener, task nudges, the morning check-in and Writing discussions all post to `/api/chat/stream` with their own prompt; they have no card to confirm anything on and nowhere to read a clarifying question, so for them the decision turn would be pure latency before a spoken word.
- **A `conversationId` in the body means the reply generates on a background thread** (`backend/delegate/runs.py`), independent of the request that asked for it — the Chat tab is the only caller that sends one. `priority.begin('chat.stream')` moves into that thread with it, acquired when the run starts and released in its `finally`, rather than living in the view/generator the way `ideas.discuss` still does. Callers with nowhere durable to put a reply (voice, nudges, morning check-in, Writing discussions) keep the original inline path: no `conversationId`, one mark spanning the view's generator exactly as before.
- SSE carries five kinds under the one `data:` frame: a bare `{tool: …}` step, `{thinking: …}`, `{content: …}`, `{messageId: …}` (persisted path only, first frame), and a final `{done: true, steps, sources, proposals}`. Steps and (on the persisted path) proposals are also written into the message's metadata by the background thread itself, not by the browser — a reload reads the same trace straight from the row instead of needing the live events to have arrived first.
- **Reasoning is shown collapsed and never persisted** (`ReasoningBlock`) — it's the model talking to itself, it dwarfs the reply, and it's rendered as plain pre-wrapped text because half-finished markdown in a reasoning trace turns into headings the model never meant.
- The **Web Search tab is gone**: searching is something the delegate decides to do mid-conversation, so making the user pick the mode up front meant asking them to predict the answer before asking the question. Old `mode='websearch'` conversations still render — they persisted the same `{steps, sources}` shape `parseAgentMeta` reads — but none can be started. There is now **one search provider for the whole app** (`research_search_*`, Settings → Research); `_migrate_websearch_search_to_research` folds a config made under the old tab into it once at startup.

#### Chat photos (`backend/chat/`, `chat_attachments`)

Photos attached to a chat message, so a meal can be photographed and dictated in one go. **There are two ways the photo reaches the model, chosen by the `llama_chat_vision` setting, and `backend/chat/context.py`'s `expand_attachments` is the only place that knows which:**

- **Directly** — Qwen3.6 _is_ a vision-language model (its GGUF carries the `image-text-to-text` tag and `rope.dimension_sections = [11, 11, 10, 0]`, Qwen-VL's four-way mRoPE). With an `mmproj` on `[qwen36]` the photo rides in as an OpenAI `image_url` content part. Strictly better where it works: the decision turn can read a nutrition label to answer the question actually asked, and a follow-up about the picture is answerable at all.
- **Read to it** — the fallback and the **default**. `backend/ai/images.read_chat_photo` has the CPU-only `[gemma4-12b-omni]` describe the photo first, and the description goes in as text. Lossy by construction: written before anyone knows what will be asked of it.

`[qwen36]` carries its own projector (`Qwen3.6-35B-A3B-mmproj-F16.gguf`, `clip.projector_type = qwen3vl_merger`) with **`mmproj-offload = false`** — the tower lives in system RAM, which is what makes it safe for throughput. Measured with it configured: **33.99 tok/s** text generation (33.7 before) and **5734 MiB** of VRAM (5732 before), so the 899 MB tower is verifiably not on the card and a text-only turn is unchanged. A photo costs prompt tokens and one CPU ViT pass — 289 tokens at 640×400, 939 at 1200×760, ~4 s and ~12 s end to end — against the whole 12B CPU generation it replaces. The app still ignores all of it until `llama_chat_vision` is ticked. **It does not retire `[gemma4-12b-omni]`**: Qwen's projector reports `clip.has_vision_encoder` only while Gemma's also reports `has_audio_encoder`, so journal audio description still needs Gemma.

**Projector filenames collide, and one already did.** Unsloth ships every repo's tower as a bare `mmproj-<precision>.gguf`, so `hf download … --local-dir ~/.cache/llama.cpp` once put Qwen3.6's projector on top of Gemma's at the same path — and nothing errored, because the path still resolved and Gemma was simply served the wrong tower, which reads as nonsense captions rather than as a failure. Both are now unambiguous: Qwen's is model-qualified (`Qwen3.6-35B-A3B-mmproj-F16.gguf`) and Gemma's lives in its own `gemma-4-12b-omni/` subdirectory. Keep new downloads in per-model subdirectories. The two are genuinely different capabilities and it is checkable — Gemma's projector reports `clip.has_audio_encoder`, Qwen's reports only `has_vision_encoder`.

Things to know:

- **`describe_image(path, *, system, prompt)` is the one vision call**, with `caption_image` (journal) and `read_chat_photo` (chat) as its two callers. They differ only in system prompt, and that difference matters: a journal caption is prose about a memory, while the chat prompt asks the model to **quote any legible text exactly** — a photographed menu or label routinely spells the very proper noun speech-to-text just mangled, which is what the transcript correction below runs on. `data_uri` is exported from that module so `chat/context.py` builds image parts off the same mime table (heic is stored but never sendable, and two copies of that rule would drift).
- **The expansion happens before `stamp_messages`, not after.** That helper rebuilds each message as `[today 21:58] <content>`, so anything not already in `content` by then never reaches the model. It is also now **part-aware**: it used to `f`-string whatever it was given, which would turn a content-part list into its own `repr` — the image silently becoming text describing a Python list. The prefix goes onto the first text part instead. Messages carrying no `attachmentIds` pass through untouched, which keeps the voice listener, nudges and Writing discussions on exactly the path they had.
- **With chat vision on, nothing pre-reads the photo** — no background captioning call, and `description_status` stays NULL rather than `'running'`, so the composer doesn't spin on work that was never queued. The transcript corrector is handed the picture itself instead (`chat_json_messages`, the one caller that needs a grammar-constrained answer _about_ an image). That is what actually removes the CPU-bound 12B from the chat path; leaving the pre-read in would have kept paying for it.
- **A photo whose reading failed says so in the prompt** ("you do not know what is in it… rather than guessing"), and on the vision path a missing file or an undecodable format becomes a text note rather than an exception. Silence there is what would have the model describe a picture it never saw; raising would cost the turn the message the photo rode on.
- **Storage is scoped by conversation** (`./data/chat/<conversation_id>/<attachment_id>.<ext>`, `CHAT_ROOT`), not by attachment, so deleting a conversation is one `delete_dir`. Uploads happen _before_ the message exists — the reading has to start while the user is still talking — so `message_id` is NULL until `add_message` claims the ids, scoped to unbound rows in that conversation so a replay can't steal a photo off an earlier message. A staged photo can be deleted; a sent one answers 409, because the reply may already have been built on it.
- **HEIC is transcoded to JPEG at the door** (`backend/imaging.py`, lifted out of `routes/food.py` — it also owns the `register_heif_opener()` call now). Browsers won't render HEIC and `images.py` refuses to send it, so converting on upload is what keeps every consumer downstream from having to know.
- One bad file in a multi-photo upload is skipped, not fatal — the user picked all of them deliberately.

#### Memory (`backend/memory.py`, `backend/routes/memory.py`, Settings → Memory)

One free-text document of standing facts, read into **every** chat system prompt as the first block in `build_chat_system_prompt` — the only block that is the same tomorrow. Its most concrete job is speech-to-text: a name written down once is a name transcribed correctly from then on.

- **Copy-on-write, non-negotiably** (`user_memory_revisions`, the `wiki_revisions`/`learning_revisions` pattern). `content` on a revision is the document as it stood _before_ that change, which is what Restore puts back. The assistant writes here without a confirm card; that trade is only defensible because every version is visible and one click from returning.
- **Capped at `MAX_CHARS` (4,000)** because it rides in every prompt, twice per turn. Past the cap `append_note` refuses with a reason the model can act on ("consolidate it before adding more") rather than truncating silently.
- **`remember` appends; `revise_memory` rewrites in the background.** The append is instant and involves no LLM, which is the common case. A revision hands `backend/ai/memory.revise_memory_document` an instruction and runs on `run_bg`; it returns **None** on any failure and the document is left untouched — a half-answer overwriting a page of standing facts is the failure that signal exists to prevent.
- The Settings editor debounces at 1.5 s and **never clobbers an in-progress edit** with a refetch (the assistant can write while the box is open).

#### Transcript correction (`backend/ai/transcript.py`, `POST /api/chat/polish-transcript`)

- **Chat dictation no longer auto-submits.** It was the one view in the app that did; every other one appends to a textarea (BrainDump/IdeaCapture/Journal/FoodCapture) so a mangled name can be fixed before it becomes a record. ChatPanel now uses the shared `useRecorder` like everything else, which also got it the mime negotiation and short-blob guard for free.
- **The pass only runs when there is something to check against** — the memory document, a photo's reading, or (with chat vision on) the photo itself. With no reference it returns the text untouched and never calls the model: asked to "improve" a transcript with nothing to compare it to, a model rewrites it, and rewriting is the one thing that must never happen to a record of what someone said. It never raises and never returns empty for the same reason.
- **`messages.raw_content` keeps the verbatim transcript** — the `journal_entries` contract exactly: NULL when typed, never overwritten, surfaced under the bubble as "As dictated". It is also where `_accept_food` gets the `raw_content` for a food entry.

#### Learning (`backend/routes/learning.py`, `src/components/Learning/`)

AI-augmented spaced repetition. All generated cards (brain-dump, journal, chat topic, verification follow-ups) land as `pending` in ONE approval queue (approve / steerable-regenerate / deny); scheduling is **FSRS** via the `fsrs` package (`backend/learning/scheduler.py`; `fsrs_state=NULL` = never reviewed/reset). A review session hands out `DECK_SIZE` (10) due cards and is **resumable**: every answered or flipped card is persisted to `learning_attempts` (one open row per card, deleted in the same transaction as its rating), so leaving the view or reloading never re-asks a card. `/due` sorts open-attempt cards first so a resumed deck always contains them; the client seeds its session from `/due` + `/attempts` and keeps local state authoritative from there. Grading is claim-coverage: cached claim decomposition → coverage check → pre-selected Again/Hard/Good/Easy the user can override. It runs **after** the answer is saved, on `backend/ai/background.py`'s single-worker executor (`backend/learning/attempts.py`) so it queues below interactive chat rather than blocking the submit; the result lands on the attempt row and the client polls it in. Answer embeddings live as float32 blobs on `learning_cards` (in-Python cosine, `backend/learning/dedup.py`) powering the approve-time duplicate **hint** (never auto-reject) and the low-similarity grading gate; both silently disable without an embedding provider. Folders bind at most one MCP evidence provider (`mcp_servers` registry) for verification — trust-first: no provider/no hit ⇒ "no authoritative source found", never open-web. Revising an active answer retires the card (`revised_from` links versions, append-only `learning_revisions` log) and resets FSRS only for semantic changes. Deletes are hard deletes; FKs null `derived_from`/`revised_from` breadcrumbs.

#### Practice (`backend/practice/`, `backend/routes/practice.py`, `src/components/Practice/`)

Typing-and-recall drills over a curated bank of 124 React/JS/HTML/CSS/DOM snippets. Freeform weighted practice, **not** spaced repetition — `backend/practice/queue.py` ranks the bank worst-first (unattempted, then inaccurate, slow, or stale) and the client fetches one snippet at a time so each pick sees the progress the last one produced. Things to know:

- **Every snippet is drilled two ways, and which one is a function of progress, not a toggle.** A **speed** drill shows the code in a read-only CodeMirror and diffs each keystroke (`src/lib/practice.ts`, wpm/accuracy). A **blind** drill shows only the snippet's `prompt` and asks for the code from memory. `backend/practice/modes.py` holds the whole policy and is pure: blind unlocks after `UNLOCK_ATTEMPTS` accurate copies, and from there the _share_ of blind runs ramps from 25% at `UNLOCK_WPM` to 75% at `FLUENT_WPM` — the better a snippet is typed, the more often it is asked for instead. The choice is deterministic (realized blind/speed counts vs. the earned share), so a whole sequence can be walked in a test and the difficulty is explainable to the person doing it. A **failed recall forces the next run back to speed**: the fix for "I couldn't remember it" is seeing it again.
- **A blind drill's `code` never leaves the server.** `_drill()` sends `prompt` instead, and the reference arrives only in the grade response — withholding it in the route rather than asking the component not to render it is what keeps the answer out of the network tab.
- **Blind grading is a reading, not a diff** (`backend/ai/practice.py`): valid syntax + does what the prompt asked, explicitly ignoring whitespace, quote style, semicolons and free identifier names, with the reference presented to the model as _one_ correct answer. A per-character diff here would mark a correct variant wrong and teach copying, which is the thing the drill exists to stop.
- **With llama-server down it degrades to `grading.fallback_grade`** — normalized text comparison — and tags the result `gradedBy: 'fallback'`, which the UI prints next to the verdict. The label is load-bearing: that verdict genuinely cannot tell a wrong answer from a differently-written correct one, and it must not pass itself off as one that can.
- **Every snippet needs a `prompt`, and its precision is the feature.** It is the only thing the writer sees and the only thing the grade is measured against, so it has to name the identifiers and literals the reference uses; an underspecified prompt reads as an unfair grade. `test_every_snippet_has_a_prompt` guards the bank.
- Blind attempts live in their own `practice_recall_attempts` table (verdict + feedback + submitted text) and their own `practice_progress` columns, so nothing over `practice_attempts` needs a mode filter to keep meaning "typing speed" — and the Stats panel reports recall on its own line rather than folding it into the averages.

#### Writing (`src/components/Writing/`, `backend/routes/writing.py`)

Two-panel layout: left nav (project list + a `WritingNav` with Chapters/Notes/Discussions sections) | full-width center panel that switches on the selected item: chapter → prose editor, note → note editor, discussion → chat view.

**DB tables**: `writing_projects`, `writing_chapters` (ordered by `position`), `writing_context_docs` (typed: `character | outline | worldbuilding | note`). "Notes" in the UI/API are stored in `writing_context_docs` (HTTP paths are `/api/writing/.../notes`; the table name is legacy). Discussions reuse the existing `conversations` + `messages` tables; `conversations.writing_project_id` scopes them to a project, and the general Chat tab filters them out (`writing_project_id IS NULL`). Deleting a project deletes its discussions.

**Chapter/note editors**: plain `<textarea>` (not CodeMirror — prose, not code) with 1.5 s debounced auto-save; chapters add live word count and font-size shortcuts. **Discussions**: full-size chat reusing `/api/chat/stream` unchanged; the frontend assembles a `systemPrompt` from the project plus checked notes. A **Summarize** button distills the transcript into a new note via `backend/ai/writing.py`.

#### Ideas (`backend/routes/ideas.py`, `src/components/Ideas/`)

The app's own feature backlog, developed with an agent instead of by hand in `docs/ROADMAP.md`. Master-detail: list + capture box on the left, idea detail on the right. Design record and the decisions the build settled — including what is deliberately _not_ built: [docs/ideas-tab.md](docs/ideas-tab.md). Things to know:

- **An idea keeps `raw_content` and `content` separately**, the same contract as `journal_entries`: `raw_content` is what was dictated or typed and is never overwritten; `content` is the AI-cleaned prose. The detail pane shows `content` when it exists and falls back to `raw_content`, with the transcript still reachable under "As captured".
- **Dictation appends to the capture box rather than saving immediately** (`useRecorder`, the `Learning/BrainDump.tsx` pattern) so a transcript can be corrected, or two thoughts recorded into one idea, before it becomes a row.
- **A sketch is a Paper _page_, not a whole paper** (`idea_sketches` → `paper_pages`), rendered straight from the page's PNG snapshot at `/api/paper/pages/<id>/image` — no copying and no new storage, the same borrowing `JournalPaperItem` does. Deleting the page cascades the sketch.
- **The caption on a sketch is the feature, not decoration.** The research agent runs on the chat model, which is text-only (`mmproj-auto = false`), so it reads the caption and the image is for the human. The `[gemma4-12b-omni]` preset added for journal attachments does not change this — it is not wired into the agent, and would not be worth a CPU-only vision call mid-loop. The UI says so out loud — a "describe this sketch" button that always errored is the journal-photo-captioning mistake.
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
- **Tool turns are never streamed.** llama-server reconstructs OpenAI `tool_calls` from the model's native notation via a grammar it picks by reading that model's chat template (so `jinja = true` is what makes tool calling model-agnostic); reassembling partial tool-call deltas across chunks is how an argument goes missing in production. Gathering and answering are separate turns so the answer can stream while gathering stays blocking. `agent.gather_events` is a generator yielding `('step', …)` then one `('result', …)`, and `agent.gather` is the blocking wrapper — the SSE route needs the generator, because with the blocking form every tool event only arrives _after_ gathering ends, which is the silent spinner the events exist to replace.

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
  - Photo captioning and audio description both need the **Multimodal input** checkbox (Settings → llama.cpp), which writes the one `gemma4-12b-omni` alias into both `llama_vision_model` and `llama_audio_model`. The chat preset sets `mmproj-auto = false` and takes text only, so both go to that separate CPU-only any-to-any model — a ~7.4 GB download, which is why it ships **off** and says so. It stayed off longer than intended: the checkbox used to write `gemma4-vision`, a preset that was never defined, so captioning had never once worked — see `backend/ai/images.py`.
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
