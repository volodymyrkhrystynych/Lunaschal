# Fanfic chapter YouTube soundtracks — design plan

Status: planning. This document describes a feature for the Fanfic/Library reader.

## Goal

Some authors include YouTube links in chapter text, usually as background soundtracks or sound effects for the scene. When the reader is viewing a chapter that contains such a link, Lunaschal should detect the link, download just the audio, and make it playable from a small persistent control bar so the user can play, stop, or loop the track while continuing to read.

## Scope

**In scope now**

- Detect plain YouTube links in chapter HTML at import time.
- Download audio only (not video) for each detected link.
- Store the downloaded audio locally under the existing fanfic data root.
- Surface a playback control bar in the reader when a YouTube link is visible on screen.
- Support play, stop, and loop for each track.
- Make the bar horizontally scrollable if there are many tracks.

**Out of scope for now**

- YouTube embeds / iframes.
- Background music that auto-plays.
- Synchronising audio to text position.
- Other video platforms.

## Current integration points

- `backend/fanfic/sanitize.py` already parses and sanitizes chapter HTML before storage.
- `src/components/Fanfic/Reader.tsx` renders `chapter.contentHtml` with `dangerouslySetInnerHTML`.
- `backend/fanfic/storage.py` provides per-fic directory helpers (`FANFIC_ROOT` → `./data/fanfic`).
- The existing `backend/fanfic/download.py` uses a single-worker drain pattern for forum imports.

## Data model (proposed)

New table `fic_chapter_youtube`:

```sql
CREATE TABLE IF NOT EXISTS fic_chapter_youtube (
    id TEXT PRIMARY KEY,
    fic_id TEXT NOT NULL REFERENCES fics(id) ON DELETE CASCADE,
    chapter_id TEXT NOT NULL REFERENCES fic_chapters(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    video_id TEXT NOT NULL,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    local_path TEXT,
    error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(chapter_id, video_id)
);

CREATE INDEX IF NOT EXISTS idx_fcy_chapter ON fic_chapter_youtube(chapter_id);
CREATE INDEX IF NOT EXISTS idx_fcy_fic ON fic_chapter_youtube(fic_id, status);
```

- `status` values: `pending`, `downloading`, `ready`, `error`.
- `title` is the YouTube video title, fetched by `yt-dlp`.
- `local_path` is the path under the per-fic data directory.
- No server-side playback state; play/pause/loop are client-only.

## Storage layout

Downloaded audio files live next to other fanfic media:

```
data/fanfic/<fic_id>/audio/<video_id>.mp3
```

This keeps a fic's soundtrack together with the fic and makes deletion easy when a fic is removed.

## YouTube link detection

At chapter import time, after `sanitize_chapter_html` runs, use `BeautifulSoup` to find all `<a href="...">` tags whose `href` matches a YouTube URL pattern.

Supported URL shapes:

- `https://www.youtube.com/watch?v=<id>`
- `https://youtu.be/<id>`
- `https://www.youtube.com/shorts/<id>`
- `https://m.youtube.com/watch?v=<id>`
- `https://www.youtube-nocookie.com/embed/<id>` (rare, but easy to support)

A small `backend/fanfic/youtube.py` module can hold the regex / URL parser:

```python
def extract_video_id(url: str) -> str | None:
    ...

def normalize_youtube_url(url: str) -> str | None:
    ...
```

For each valid link, insert a `fic_chapter_youtube` row with `status='pending'`. The original link remains in the HTML so users can still open it on YouTube.

## Download pipeline

Add a `backend/fanfic/youtube_download.py` worker:

1. Read the next `pending` row from `fic_chapter_youtube`.
2. Call `yt-dlp` to extract the best audio stream:

   ```
   yt-dlp -x --audio-format mp3 -o "<fic_dir>/audio/%(id)s.%(ext)s" <url>
   ```

3. Update the row:
   - `status='ready'` and `local_path=...` on success.
   - `status='error'` and `error=<message>` on failure.

Run this from a background thread with the same single-worker drain pattern used for fanfic imports (`download.py`). This avoids hammering YouTube and keeps resource use predictable.

## API endpoints (proposed)

- `GET /api/fanfic/<fic_id>/chapters/<chapter_id>/youtube` — list tracks for the chapter, including `id`, `videoId`, `title`, `status`, `error`, and a `downloadUrl` if `status='ready'`.
- `GET /api/fanfic/<fic_id>/youtube/<video_id>/audio` — stream the audio file. Should support HTTP `Range` requests so the browser can seek.

The streaming route can use `send_file(..., conditional=True)` in Flask (or a dedicated range-serving helper if needed).

## Frontend: playback bar

### When it appears

In `Reader.tsx`, after the chapter HTML is mounted, use an `IntersectionObserver` on the detected YouTube links inside `chapter.contentHtml`. When any of those links enters the viewport, the playback bar appears and lists tracks for the current chapter. The bar stays visible while the user is on that chapter, even if the user scrolls past the link, so the track can keep playing.

### Bar design

A fixed or sticky bottom bar inside the reader pane, similar in feel to the existing `SttPanel`.

Each track card in the bar shows:

- **Title** — the YouTube video title, if known; otherwise the link text or the video ID. Truncated with `…` if too long.
- **Back link / context** — a small link back to the chapter or fic title (the user called this "the link back to the book chapters"). Clicking it could jump to the chapter list or top of the page.
- **Play / Pause button** — toggles playback.
- **Stop button** — stops playback and resets to the beginning.
- **Loop toggle** — when on, the track repeats when it finishes.

If the chapter has multiple YouTube links, each gets its own card in the bar. The bar should be a horizontally scrollable container (`overflow-x-auto`, `flex-nowrap`) so many cards do not wrap or push each other off screen.

### Playback behaviour

- Only one track plays at a time; starting a second track pauses the first.
- Playback continues while the user scrolls within the chapter.
- If the user leaves the chapter, playback stops.
- If the track is not downloaded yet, the Play button triggers the download and shows a spinner until the status flips to `ready`.
- Errors are shown with a small error icon and a Retry button.

## UI states

| Status        | Bar shows                                                    |
| ------------- | ------------------------------------------------------------ |
| `pending`     | Track title + a download/Play button that starts the worker. |
| `downloading` | Track title + a spinner + "Downloading…".                    |
| `ready`       | Track title + Play/Stop/Loop controls.                       |
| `error`       | Track title + error icon + Retry button.                     |

## Audio format

`yt-dlp` can output a single audio file. For maximum browser compatibility, transcode to **MP3** with a fixed bitrate (e.g., 128 kbps). This is a good balance between quality and storage for background music. Later we can add `ogg`/`opus` if needed.

## Keyboard shortcuts (optional)

The reader already uses a keyboard shortcut system (`src/shortcuts/`). Potential additions:

- `M` — toggle the soundtrack bar.
- `,` / `.` or `←` / `→` — previous / next track in the bar.
- `L` — toggle loop for the active track.

Keep these as optional; the primary interface is the mouse/touch bar.

## Open questions

- Should we pre-download all tracks for the current chapter when the chapter opens, or only on first play?
- Should the bar be global (one sound at a time across the whole app) or per-reader instance?
- Should loop preference be persisted in `localStorage` or reset per session?
- How do we handle deleted/blocked YouTube videos that can no longer be downloaded?
- Should we support chapters that link to the same video multiple times? The `UNIQUE(chapter_id, video_id)` constraint treats them as one track.
- What happens if a fic has many chapters, each with many soundtracks? Should there be a bulk "download all" or per-fic settings?

## Next steps

1. Add `fic_chapter_youtube` to `backend/db/schema.sql`.
2. Add `yt-dlp` to `requirements.txt`.
3. Create `backend/fanfic/youtube.py` for URL parsing and `backend/fanfic/youtube_download.py` for the worker.
4. Hook link detection into the chapter import pipeline in `backend/fanfic/download.py` / `sanitize.py`.
5. Add `GET .../youtube` and `GET .../audio` routes in `backend/routes/fanfic.py`.
6. Add the playback bar component in `src/components/Fanfic/Reader.tsx`.
