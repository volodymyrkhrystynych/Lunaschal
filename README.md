# Lunaschal

A self-hosted personal knowledge management app with local AI. Journal, calendar, flashcards, and an AI chat that understands your notes — all running on your own machine.

## Features

- **AI Chat** — Streaming chat with your choice of LLM. The assistant automatically detects when you're writing a journal entry or logging an event and offers to save it for you. Ask "quiz me on X" to get flashcards on any topic.
- **Journal** — Write and search personal entries. Full-text search powered by SQLite FTS5.
- **Calendar** — Activity log for events and appointments, linked to journal entries.
- **Flashcards** — Spaced repetition (SM-2 algorithm) with AI-generated cards from your journal entries or any topic.
- **RAG** — The chat retrieves semantically relevant journal entries as context for each message using local vector embeddings.
- **Voice Input** — Global speech-to-text shortcut (Right Ctrl). Works system-wide, types transcribed text at your cursor. Supports local (faster-whisper, GPU) or OpenAI Whisper API backends.
- **Voice Assistant** — Right Alt triggers a voice conversation: speech → AI chat → spoken reply. TTS via Kokoro (local, CPU, ~80 MB) or OpenAI API. Also activatable by saying "Hey Luna" (wake word, requires one-time model generation).
- **Morning Check-in** — On wake-from-sleep between 8–11 AM, a voice conversation prompts you to rubber-duck your plans for the day.

## Stack

| Layer | Tech |
|---|---|
| Frontend | React 19, Vite, Tailwind CSS v4 |
| Backend | Hono (Node.js), tRPC v11 |
| Database | SQLite (better-sqlite3, Drizzle ORM, FTS5, sqlite-vec) |
| AI / LLM | Vercel AI SDK — OpenAI, Google Gemini, or Ollama |
| STT | faster-whisper (`large-v3-turbo`) via Python/FastAPI |

## Getting Started

### Prerequisites

- Node.js 18+ and npm
- An AI provider: OpenAI API key, Google Gemini API key, or [Ollama](https://ollama.com) running locally

### Install and run

```bash
npm install
npm run dev
```

The server runs on `http://localhost:7842`. In dev mode, Vite runs on `http://localhost:5173` and proxies API requests to the server.

On first launch you'll be prompted to set a password and configure your AI provider.

### Production

```bash
npm run build
npm run start
```

The server serves the built frontend from `dist/` and listens on port 3000 (override with `PORT` env var).

## AI Providers

Configure your provider in the Settings page or via environment variables:

| Provider | Env var | Default model |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `gpt-4o` |
| Google Gemini | `GOOGLE_API_KEY` | `gemini-2.0-flash` |
| Ollama | — (set URL in Settings) | `llama3.2` |

> RAG (vector embeddings) requires OpenAI or Gemini. Ollama embeddings are not yet supported.

## Voice Input (STT + TTS)

A Python sidecar (FastAPI on port 8765) handles speech-to-text and text-to-speech. Two backends are available:

| Backend | Best for | What gets installed |
|---------|----------|---------------------|
| `local` | GPU machine, fully offline | faster-whisper (~1.5 GB), kokoro-onnx (~80 MB), openwakeword |
| `openai` | Low-power/laptop, cloud | openai Python client only |

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

**Configure** — add to your `.env` in the repo root (loaded automatically by the run scripts):
```env
# Required for openai backend
OPENAI_API_KEY=sk-...

STT_BACKEND=openai        # local | openai  (default: local)
TTS_BACKEND=openai        # local | openai  (default: local)
OPENAI_TTS_VOICE=nova     # alloy | echo | fable | onyx | nova | shimmer
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
- **Right Ctrl** — record → transcribe → paste at cursor
- **Right Alt** — record → AI chat → spoken reply (voice assistant)
- **"Hey Luna"** — wake word → record → AI chat → spoken reply (see below)

The STT service also exposes `POST /api/transcribe` on the main server (proxied from `http://127.0.0.1:8765`).

### Wake Word ("Hey Luna")

Say "Hey Luna" to start a voice conversation without touching the keyboard. The listener records until you stop speaking (1.5 s of silence), then responds.

**One-time model generation** (~5–10 min, downloads TTS models):
```bash
./stt/.venv/bin/python stt/generate_wake_word.py
```

This writes `stt/models/hey_luna.onnx`. Then enable it by setting `WAKE_WORD_MODEL`:
```bash
WAKE_WORD_MODEL=stt/models/hey_luna.onnx ./stt/run_listener.sh
```

Add the variable to your shell config or a `.env` file to make it permanent.

Environment variables:

| Variable | Default | Description |
|---|---|---|
| `WAKE_WORD_MODEL` | — | Path to `.onnx` wake word model; detection disabled if unset |
| `WAKE_WORD_THRESHOLD` | `0.5` | Detection confidence threshold (0–1) |
| `WAKE_SILENCE_RMS` | `0.015` | RMS energy below this is considered silence |
| `WAKE_SILENCE_SECS` | `1.5` | Seconds of silence before auto-stopping the recording |

### Morning Check-in

```bash
# Run as a background daemon (detects wake-from-sleep automatically)
./stt/run_morning_checkin.sh

# Run immediately (for testing)
./stt/run_morning_checkin.sh --now
```

When the computer wakes from sleep between 8 AM and 11 AM, the daemon starts a short voice conversation asking what you plan to work on. Runs once per day (tracks via a flag in `$XDG_RUNTIME_DIR`).

Environment variables: `MORNING_START_HOUR` (default `8`), `MORNING_END_HOUR` (default `11`), `STT_URL`, `LUNASCHAL_URL`.

## Database

SQLite database stored at `./data/lunaschal.db`. Migrations run automatically on server start.

```bash
npm run db:generate   # generate migration after schema changes
npm run db:migrate    # apply migrations manually
npm run db:studio     # open Drizzle Studio in the browser
```

## Auth

Single-user, password-protected. Uses a bcrypt-hashed password stored in the database and a 7-day JWT cookie.

Auth is skipped for localhost in development (`NODE_ENV !== 'production'`). Set `JWT_SECRET` in production.

## Environment Variables

**Server:**

| Variable | Default | Description |
|---|---|---|
| `PORT` | `3000` | Server port |
| `DATABASE_URL` | `./data/lunaschal.db` | SQLite file path |
| `JWT_SECRET` | dev default | Change in production |
| `OPENAI_API_KEY` | — | LLM provider fallback; also used by STT/TTS when backend=openai |
| `GOOGLE_API_KEY` | — | Fallback if not set in Settings |
| `STT_SERVICE_URL` | `http://127.0.0.1:8765` | STT sidecar URL |

**STT/TTS sidecar** (read from `.env` automatically):

| Variable | Default | Description |
|---|---|---|
| `STT_BACKEND` | `local` | `local` or `openai` |
| `TTS_BACKEND` | `local` | `local` or `openai` |
| `OPENAI_TTS_VOICE` | `nova` | OpenAI TTS voice (alloy/echo/fable/onyx/nova/shimmer) |
| `OPENAI_STT_MODEL` | `whisper-1` | OpenAI STT model |
| `OPENAI_TTS_MODEL` | `tts-1` | OpenAI TTS model |
| `WHISPER_MODEL` | `large-v3-turbo` | Local STT model |
| `WHISPER_DEVICE` | `cuda` | Local STT device (`cuda` or `cpu`) |
| `WAKE_WORD_MODEL` | — | Path to `.onnx` wake word model; disabled if unset |
| `WAKE_WORD_THRESHOLD` | `0.5` | Wake word detection confidence threshold |
| `WAKE_SILENCE_RMS` | `0.015` | Silence energy threshold for auto-stop |
| `WAKE_SILENCE_SECS` | `1.5` | Seconds of silence before recording stops |
