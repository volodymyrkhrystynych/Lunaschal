# STT (Speech-to-Text) (`stt/`, `backend/routes/stt.py`)

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

All three shortcuts are rebindable in Settings → Voice Shortcuts (stored in the `settings` table; env vars are fallbacks). The listener also runs the task-nudge loop (see Tasks & todos in the root CLAUDE.md). Every transcription is logged to the `transcriptions` table; `POST /api/transcribe/correct` re-runs a transcript through the LLM for cleanup — it always consults `backend/memory.py`'s standing document as reference material now, on top of whatever `ground_truth` text is optionally pasted in via the Editor's STT panel. None of the three shortcuts above call this route themselves (`stt/listener.py` and `stt/morning_checkin.py` both use plain `/api/transcribe`) — it's a manual, on-demand pass, not something dictation runs through automatically.

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

## Morning Check-in (`stt/morning_checkin.py`)

Daemon that monitors for wake-from-sleep events via a time-jump trick (sleeps 10 s; if the wall clock advanced >30 s, the system was suspended). When the machine wakes between `MORNING_START_HOUR` (default 8) and `MORNING_END_HOUR` (default 11), it starts a voice conversation that helps the user rubber-duck their plans for the day. A flag file in `$XDG_RUNTIME_DIR` prevents duplicate check-ins within the same calendar day.

Env vars: `STT_URL`, `LUNASCHAL_URL`, `MORNING_START_HOUR`, `MORNING_END_HOUR`.
