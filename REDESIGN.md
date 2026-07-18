# Lunaschal Desktop App Design

**Date:** 2026-06-22  
**Completed:** 2026-06-23  
**Status:** Done

---

## Goal

Rebuild Lunaschal as a native desktop app with a Python backend. PyWebView opens a native window around a Flask server. The React frontend stays; only the API layer changes. The desktop app is the primary product — a later phase exposes it as a server so a laptop browser can connect over LAN.

---

## Stack

| Layer         | Current                   | New                                               |
| ------------- | ------------------------- | ------------------------------------------------- |
| Desktop shell | —                         | PyWebView                                         |
| Backend       | Hono (Node.js)            | Flask (Python)                                    |
| API layer     | tRPC                      | REST (JSON over HTTP)                             |
| Database      | Drizzle + better-sqlite3  | Python `sqlite3` (built-in)                       |
| AI            | Vercel AI SDK             | OpenAI / google-generativeai / ollama Python SDKs |
| Frontend      | React + Vite + Tailwind   | React + Vite + Tailwind (unchanged)               |
| STT service   | stt/service.py (separate) | stt/service.py (kept separate, unchanged)         |

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  main.py (PyWebView entry point)                     │
│                                                      │
│  1. Start Flask in a background thread               │
│  2. Poll GET /api/health until ready                 │
│  3. webview.create_window(url='http://127.0.0.1:5000')│
│  4. webview.start()  ← blocks until window closed   │
└──────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────┐     ┌──────────────────────┐
│  Flask (port 5000)   │     │  stt/service.py      │
│  - serves dist/      │     │  (port 8765)         │
│  - REST API routes   │     │  started separately  │
│  - SSE chat stream   │     │  or by main.py       │
│  - proxies /api/     │     └──────────────────────┘
│    transcribe → 8765 │
└──────────────────────┘
         ▲
         │ fetch (REST JSON)
┌──────────────────────┐
│  React (in webview)  │
│  built to dist/      │
│  React Query for     │
│  data fetching       │
└──────────────────────┘
```

---

## Backend Structure

```
backend/
  app.py              # Flask app factory, registers blueprints
  main.py             # PyWebView entry point (or keep at root)
  auth.py             # PyJWT + bcrypt, same logic as current auth.ts
  db/
    connection.py     # sqlite3 connection, runs migrations on startup
    schema.sql        # table definitions (ported from schema.ts)
    fts.py            # FTS5 virtual table setup
    vectors.py        # sqlite-vec extension (has Python bindings)
  routes/
    journal.py        # Blueprint: GET/POST/DELETE /api/journal
    calendar.py
    flashcard.py
    settings.py
    rag.py
    files.py          # new: file browser routes
    chat.py           # SSE streaming via stream_with_context
  ai/
    provider.py       # resolves active provider from DB settings
    embeddings.py     # OpenAI / Gemini embeddings
    rag.py            # semantic search + context formatting
    classifier.py     # intent classification (journal / calendar / etc.)
    flashcards.py
```

`server/` (Node.js) is deleted. `backend/` replaces it entirely.

---

## Frontend Changes

**Drop tRPC.** The tRPC client (`src/hooks/trpc.ts`) and all `trpc.*` hook calls in components are replaced with React Query + plain `fetch`. React Query (`@tanstack/react-query`) is already installed — only the query functions change.

Example migration:

```ts
// Before (tRPC)
const { data } = trpc.journal.list.useQuery();

// After (fetch + React Query)
const { data } = useQuery({
  queryKey: ['journal'],
  queryFn: () => fetch('/api/journal').then(r => r.json()),
});
```

Mutations follow the same pattern with `useMutation`. The component JSX is untouched — only the data-fetching call changes.

The Vite dev proxy stays: `/api → http://localhost:5000` (was 7842, now 5000).

---

## Database

Python's built-in `sqlite3` module — no ORM for MVP. Raw SQL keeps the dependency count low and the queries readable. Schema is ported from `schema.ts` to `schema.sql`; all IDs stay as ULIDs (via the `python-ulid` package).

**FTS5**: same SQLite FTS5 virtual table, created in `db/fts.py` on first run.

**Vector search**: `sqlite-vec` has an official Python package (`sqlite-vec`). Load it the same way as the current Node.js code — `conn.enable_load_extension(True)` then load the `.so`.

**Migrations**: for MVP, a simple version table + sequential `.sql` files run on startup. No migration framework needed yet.

---

## AI Layer

| Feature                    | Python library                                                                                  |
| -------------------------- | ----------------------------------------------------------------------------------------------- |
| OpenAI (chat + embeddings) | `openai`                                                                                        |
| Google Gemini              | `google-generativeai`                                                                           |
| Ollama                     | `ollama` (or direct HTTP)                                                                       |
| Streaming chat             | Flask `Response` with a generator + `stream_with_context`                                       |
| Intent classification      | `openai` / `google-generativeai` with structured output (same logic as current `classifier.ts`) |

---

## Auth

Same approach: bcrypt password hash stored in settings table, PyJWT for the cookie token. `flask-jwt-extended` or bare `PyJWT` — bare PyJWT matches what's currently used and avoids an extra dependency.

Auth bypass for localhost in non-production mode stays.

---

## Dev Workflow

```bash
# Terminal 1 — Flask backend
cd backend && flask run --port 5000

# Terminal 2 — Vite frontend (HMR, proxies /api to Flask)
npm run dev:client

# Open browser at http://localhost:5173 — same as today
```

PyWebView is only needed when testing the packaged desktop experience:

```bash
python main.py --dev   # opens PyWebView window loading localhost:5173
```

In production, `python main.py` starts Flask, builds nothing (expects `dist/` to exist), and opens the window at `http://127.0.0.1:5000`.

---

## Packaging

`pyinstaller` bundles the Python app (Flask + PyWebView + all dependencies) into a single executable. The React build (`dist/`) is included as data files.

```
dist/                  # Vite build output (bundled into executable)
lunaschal              # PyInstaller output — single binary, runs anywhere Python deps are met
```

The STT service and model weights are not bundled (too large, GPU-dependent). `main.py` optionally spawns `stt/service.py` as a subprocess on startup if Python deps are available; otherwise skips silently.

---

## Phased Delivery

### Phase 1 ✅ — Flask shell + PyWebView window (1 day)

- Create `backend/app.py` with a single `/api/health` route
- `main.py`: start Flask in a thread, poll health, open PyWebView window
- Serve the existing `dist/` from Flask
- Confirm the window opens and loads the React app
- Set up `pyproject.toml` / `requirements.txt` for the Python side

### Phase 2 ✅ — Port all backend routes (2–3 days)

- `db/connection.py` + `schema.sql` (SQLite setup, migrations)
- All blueprints: journal, calendar, flashcard, settings, rag, chat (SSE)
- Auth (PyJWT + bcrypt)
- Replace tRPC hooks in all frontend components with React Query + fetch
- Delete `server/` (Node.js)

### Phase 3 ✅ — File editor + STT panel (1–2 days)

- `routes/files.py` (list/read/write/rename/soft-delete, path traversal guard)
- `src/components/Editor/` (FileTree, EditorPane with CodeMirror 6, SttPanel)
- Add Files view to sidebar
- SttPanel lifted to `App.tsx` level so it's always visible
- Auto-save with debounce

### Phase 4 ✅ — Network / server mode (half day)

- Bind Flask to `0.0.0.0` via settings toggle
- `STT_AUTH_TOKEN` shared secret forwarded through the transcribe proxy
- Laptop opens browser, gets full UI

---

## What Stays the Same

- All React components (JSX unchanged; only data-fetching calls swap)
- Tailwind styles and CSS custom properties
- `stt/service.py` and `stt/listener.py` — untouched
- Morning check-in daemon
- SQLite database file location (`./data/lunaschal.db`)
- Overall app structure and all existing views
