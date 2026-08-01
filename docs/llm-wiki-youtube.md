# LLM wiki — YouTube download and transcription plan

Status: planning. A design sketch for ingesting YouTube videos into an LLM-backed wiki.

## Goal

Turn YouTube videos into searchable, linkable, LLM-readable wiki pages. A user pastes a URL, the pipeline fetches the video, extracts the audio, transcribes it, chunks it, and stores it so the wiki can quote sources and answer questions grounded in the video.

## Why YouTube

- A lot of valuable knowledge (talks, tutorials, lectures, interviews) is only available as video.
- Transcripts make that knowledge searchable and quotable.
- Local processing keeps the source material private.

## Pipeline overview

```
YouTube URL
    │
    ▼
yt-dlp — download best audio or video
    │
    ▼
ffmpeg — extract audio, resample to 16 kHz mono
    │
    ▼
transcriber (faster-whisper / Parakeet / whisper.cpp)
    │
    ▼
transcript text + word-level timestamps (optional)
    │
    ▼
chunker — split by silence, sentence, or fixed window
    │
    ▼
wiki page — title, metadata, summary, transcript, source link
```

## Components

### 1. Downloader

- Tool: `yt-dlp` (Python) or its library.
- Config: max resolution, audio-only by default, prefer `m4a`/`opus`, fall back to video + audio extract.
- Metadata: title, channel, upload date, duration, description, tags, thumbnail, original URL.
- Storage: keep the original media under `./data/wiki/media/<video-id>/`.

### 2. Audio extraction

- `ffmpeg` to 16 kHz mono WAV or FLAC.
- If the video has a speech-only track, prefer it.

### 3. Transcription

- **Local GPU/CPU**: `faster-whisper` (reuses the existing meeting transcription setup in `backend/meetings/`).
- **Low-power fallback**: Parakeet (CPU) or an OpenAI API key.
- Output: plain text, segments with start/end timestamps, optional speaker diarization if we ever want it.

### 4. Chunking

Goal: each chunk should be a self-contained unit the LLM can retrieve and quote.

- Split on long silence first.
- Then split on sentence boundaries.
- Fixed max token budget (e.g., 300–500 tokens) with a small overlap.
- Keep a mapping: `chunk → original video id + start/end time`.

### 5. Wiki page generation

For each video, create a wiki page:

- Title and channel.
- Original URL and local media path.
- AI-generated short summary.
- Key points / outline.
- Full transcript (collapsible), time-stamped.
- Chunks linked to RAG store.

### 6. RAG / Q&A

- Store chunks with embeddings in the same vector/embedding store the wiki uses.
- When the user asks a question, retrieve chunks, then have the LLM answer with citations like `[Video Title, 12:34]`.
- A chat sidebar can answer against one or more videos.

## Storage

- `wiki_videos` table: id, source URL, title, channel, duration, metadata JSON, local media path, transcript status.
- `wiki_video_chunks` table: video id, chunk index, start time, end time, text, embedding blob (float32).
- Media files under `./data/wiki/media/<video-id>/`.
- Transcripts as plain text files, not DB blobs, for large videos.

## AI prompts

- **Summary prompt**: summarize the video in 3–5 bullet points, preserve key technical details.
- **Key-claims prompt**: extract claims, definitions, and how-to steps.
- **Q&A prompt**: answer using only the provided transcript chunks; cite timestamps.

## Offline / cost options

| Setup     | Tools                           | Notes                                             |
| --------- | ------------------------------- | ------------------------------------------------- |
| Local GPU | faster-whisper + local LLM      | Best for privacy and batch processing.            |
| Local CPU | Parakeet + whisper.cpp / kokoro | Slower but fully offline.                         |
| Cloud API | OpenAI Whisper + OpenAI chat    | Fast, costs money, sends audio/text to the cloud. |

## Error handling

- `download_failed`, `audio_extraction_failed`, `transcription_failed`, `too_long`, `no_speech`.
- Each status stored on the wiki video row so the user can retry or delete.

## Copyright / policy notes

- Only download content the user has the right to process.
- Keep original URLs prominent.
- Do not re-upload or redistribute media; local storage only.
- Captions-only mode: if a transcript is already available via `yt-dlp --write-auto-subs`, optionally skip audio transcription.

## Open questions

- Should the wiki support audio-only ingestion (podcasts, lectures) the same way?
- How do we update a page if the uploader replaces the video?
- Do we auto-transcribe on paste, or queue for background processing?
- Should we support multiple languages and translation?

## Next steps

1. Build a minimal `yt-dlp` → `ffmpeg` → `faster-whisper` end-to-end script.
2. Define the `wiki_videos` and `wiki_video_chunks` schema.
3. Wire the chunks into the wiki's existing RAG / chat pipeline.
4. Add a simple "Paste YouTube URL" input in the wiki UI.
