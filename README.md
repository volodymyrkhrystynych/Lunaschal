# Lunaschal

A self-hosted personal knowledge management desktop app with local AI. Journal, calendar, flashcards, file editor, creative writing workspace, and an AI chat that understands your notes — all running on your own machine as a native desktop window.

## Features

- **AI Chat** — Streaming chat with your choice of LLM. The assistant automatically detects when you're writing a journal entry or logging an event and offers to save it for you. Ask "quiz me on X" to get flashcards on any topic.
- **Journal** — Write and search personal entries. Full-text search powered by SQLite FTS5. Define curated tags in Settings → Tags; the AI scans all existing entries to apply each new tag retroactively. Filter the journal by curated tag via pill buttons above the entry list.
- **Calendar** — Activity log for events and appointments, linked to journal entries.
- **Flashcards** — Spaced repetition (SM-2 algorithm) with AI-generated cards from your journal entries or any topic.
- **RAG** — The chat retrieves semantically relevant journal entries as context for each message using vector embeddings.
- **Writing** — Creative writing workspace with projects, ordered chapters, and a context document library (character sheets, outlines, world-building notes, etc.). A scoped AI chat sidebar lets you discuss plot and story; you choose which context docs the AI can see via checkboxes.
- **File Editor** — Browse, create, edit, and rename files under a configurable root directory (`~/notes` by default). CodeMirror 6 editor with syntax highlighting for Markdown, JavaScript/TypeScript, and Python. Auto-saves after 1.5 s of inactivity.
- **Voice Input** — Record audio from the browser mic and transcribe it into the active editor or clipboard via the persistent bottom bar. Global keyboard shortcuts (F1, Right Alt) work system-wide via the background listener.
- **Voice Assistant** — Right Alt triggers a voice conversation: speech → AI chat → spoken reply. TTS via Kokoro (local, CPU, ~80 MB) or OpenAI API. Also activatable by saying "Hey Luna" (wake word).
- **Morning Check-in** — On wake-from-sleep between 8–11 AM, a voice conversation prompts you to rubber-duck your plans for the day.
- **Network / server mode** — Expose the app on your LAN so a laptop browser can connect. Protected by password + a rotating display code (pseudo-2FA).

## Stack

| Layer         | Tech                                                  |
| ------------- | ----------------------------------------------------- |
| Desktop shell | PyWebView                                             |
| Frontend      | React 19, Vite, Tailwind CSS v4                       |
| Backend       | Flask (Python)                                        |
| API layer     | REST (JSON over HTTP) + React Query                   |
| Database      | SQLite (`sqlite3` built-in, FTS5, sqlite-vec)         |
| AI / LLM      | `openai`, `google-generativeai`, `ollama` Python SDKs |
| STT/TTS       | faster-whisper + kokoro-onnx (local) or OpenAI API    |

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+ and npm
- An AI provider: OpenAI API key, Google Gemini API key, or [Ollama](https://ollama.com) running locally

### Install and run

```bash
# Python dependencies
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Frontend dependencies
npm install

# Start Flask backend + Vite dev server
npm run dev
```

The Flask backend runs on `http://localhost:5000`. Vite runs on `http://localhost:5173` and proxies `/api` requests to Flask. Open `http://localhost:5173` in your browser.

To open as a native desktop window instead:

```bash
# Dev mode — PyWebView loads the Vite dev server
python main.py --dev

# Production mode — build first, then open the PyWebView window
npm run build
python main.py
```

### Production

```bash
npm run build
python main.py
```

Flask serves the built `dist/` from `http://127.0.0.1:5000` and PyWebView opens it as a native window.

## AI Providers

Configure your provider in the Settings page. The active provider and model are stored in the database and can be changed at any time.

| Provider      | Env var (fallback)      | Notes                                          |
| ------------- | ----------------------- | ---------------------------------------------- |
| OpenAI        | `OPENAI_API_KEY`        | Also used by STT/TTS when `STT_BACKEND=openai` |
| Google Gemini | `GOOGLE_API_KEY`        | —                                              |
| Ollama        | — (set URL in Settings) | No embeddings support yet                      |

> RAG (vector embeddings) requires OpenAI or Gemini. Ollama embeddings are not yet supported.

## Voice Input (STT + TTS)

A Python sidecar on port 8765 handles speech-to-text and text-to-speech. Two backends are available:

| Backend  | Best for                   | What gets installed                                          |
| -------- | -------------------------- | ------------------------------------------------------------ |
| `local`  | GPU machine, fully offline | faster-whisper (~1.5 GB), kokoro-onnx (~80 MB), openwakeword |
| `openai` | Low-power/laptop, cloud    | openai Python client only                                    |

Backends can be mixed — e.g. `STT_BACKEND=openai TTS_BACKEND=local`.

**System packages** (Arch Linux):

```bash
sudo pacman -S wtype portaudio
```

**Setup** (one time):

```bash
# Local GPU setup
bash stt/setup.sh

# API setup (no heavy model downloads)
bash stt/setup.sh --api
```

**Run:**

```bash
# Terminal 1 — STT+TTS service
./stt/run_service.sh

# Terminal 2 — global keyboard listener
./stt/run_listener.sh

# Or start both together:
npm run stt
```

**Shortcuts:**

- **F1** — record → transcribe → paste at cursor (system-wide)
- **Right Alt** — record → AI chat → spoken reply (voice assistant)
- **"Hey Luna"** — wake word → voice conversation (see below)

The in-app STT bar (always visible at the bottom of the window) records from the browser mic and either inserts the transcription at the editor cursor (Files view) or copies it to the clipboard (other views).

### Wake Word ("Hey Luna")

**One-time model generation** (~5–10 min):

```bash
./stt/.venv/bin/python stt/generate_wake_word.py
```

This writes `stt/models/hey_luna.onnx`. Enable it:

```bash
WAKE_WORD_MODEL=stt/models/hey_luna.onnx ./stt/run_listener.sh
```

### Morning Check-in

```bash
# Background daemon (auto-detects wake-from-sleep)
./stt/run_morning_checkin.sh

# Run immediately (testing)
./stt/run_morning_checkin.sh --now
```

When the computer wakes from sleep between 8 AM and 11 AM, the daemon starts a short voice conversation asking what you plan to work on. Runs once per day.

## File Editor

The Files view browses files under `FILES_ROOT` (default: `~/notes`, created automatically). Files can be created, renamed, and soft-deleted (moved to `~/notes/.trash`). The editor auto-detects language from the file extension and auto-saves after 1.5 s of inactivity.

Override the root:

```bash
FILES_ROOT=/home/you/documents python main.py
```

## Network / Server Mode

Expose Lunaschal on your LAN so a laptop browser can connect.

**On the server machine:**

```bash
export NETWORK_MODE=1
export LUNASCHAL_PASSWORD=your-password
export JWT_SECRET=random-string   # recommended
python main.py
```

**On the laptop:** navigate to `http://<server-ip>:5000`.

The login form asks for:

1. **Password** — the `LUNASCHAL_PASSWORD` env var set on the server
2. **Display code** — the 6-digit code shown in Settings → Network Access on the server machine

Both factors are required. The display code can be regenerated from Settings after each session. Localhost (the desktop window) always bypasses auth entirely.

To also proxy STT audio from the laptop to the server's STT service, set `STT_AUTH_TOKEN` on both the server and the sidecar.

## Database

SQLite stored at `./data/lunaschal.db` (override with `DATABASE_URL`). Schema migrations run automatically on every server start — no manual steps needed.

## Environment Variables

**Flask backend:**

| Variable             | Default                 | Description                                   |
| -------------------- | ----------------------- | --------------------------------------------- |
| `DATABASE_URL`       | `./data/lunaschal.db`   | SQLite file path                              |
| `JWT_SECRET`         | dev default             | Set in production to a random string          |
| `NETWORK_MODE`       | —                       | Set to `1` to bind `0.0.0.0` and enforce auth |
| `LUNASCHAL_PASSWORD` | —                       | Required when `NETWORK_MODE=1`                |
| `STT_SERVICE_URL`    | `http://127.0.0.1:8765` | STT sidecar base URL                          |
| `STT_AUTH_TOKEN`     | —                       | Bearer token forwarded to the STT sidecar     |
| `FILES_ROOT`         | `~/notes`               | Root directory for the file editor            |
| `OPENAI_API_KEY`     | —                       | Fallback if not set in Settings               |
| `GOOGLE_API_KEY`     | —                       | Fallback if not set in Settings               |

**STT/TTS sidecar:**

| Variable              | Default                 | Description                                        |
| --------------------- | ----------------------- | -------------------------------------------------- |
| `STT_BACKEND`         | `local`                 | `local` or `openai`                                |
| `TTS_BACKEND`         | `local`                 | `local` or `openai`                                |
| `OPENAI_API_KEY`      | —                       | Required for openai backends                       |
| `OPENAI_TTS_VOICE`    | `nova`                  | alloy / echo / fable / onyx / nova / shimmer       |
| `WHISPER_MODEL`       | `large-v3-turbo`        | Local STT model                                    |
| `WHISPER_DEVICE`      | `cuda`                  | `cuda` or `cpu`                                    |
| `WAKE_WORD_MODEL`     | —                       | Path to `.onnx` wake word model; disabled if unset |
| `WAKE_WORD_THRESHOLD` | `0.5`                   | Wake word detection confidence (0–1)               |
| `WAKE_SILENCE_RMS`    | `0.015`                 | Silence energy threshold for auto-stop             |
| `WAKE_SILENCE_SECS`   | `1.5`                   | Seconds of silence before recording stops          |
| `LUNASCHAL_URL`       | `http://127.0.0.1:5000` | Chat server URL (used by listener)                 |
| `MORNING_START_HOUR`  | `8`                     | Morning check-in window start                      |
| `MORNING_END_HOUR`    | `11`                    | Morning check-in window end                        |
