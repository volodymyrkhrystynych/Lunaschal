# Toronto Star PDF reader

Settings → General → Newspapers imports one or multiple subscriber-downloaded
Toronto Star PDFs. Select the files, review each issue date (suggested from
YYYYMMDD or YYYY-MM-DD filenames), fill in missing dates, then import. Uploads
run sequentially with a separate result per file; failures can be retried without
re-uploading successful files. Duplicate dates within a batch must be resolved,
and already archived issues are kept with their existing markup.

In Newspapers, tap the Toronto Star cover or Read Toronto Star PDF. The archive
dropdown opens older issues.

The reader fits each page to the available width and scrolls vertically. Read
mode supports normal browser touch scrolling and pinch zoom. Pen and Highlight
modes accept Apple Pencil; fingers scroll without adding marks. Undo removes the
last stroke. Export PDF downloads a copy with the current markup drawn into it.
It does not modify newspaper text or replace the original PDF.

Markup saves to SQLite every 1.5 seconds, with a local browser draft written after
each stroke. The status distinguishes local storage from a completed server save.
An optimistic revision check prevents two readers from overwriting each other.
A conflicting local draft remains available for export. Close with local draft
preserves it on the current browser; it does not promise a server save. Reading
requires access to the Lunaschal server; this is not an offline PDF cache.

## Storage

Set `NEWSPAPERS_ARCHIVE_ROOT` to the archive directory on the server before
starting Lunaschal, for example `/media/expansion/newspapers`. It defaults to
`NEWSPAPERS_ROOT` (`./data/newspapers`). PDFs live in `toronto-star/YYYY-MM-DD.pdf`;
metadata and markup live in the main database. No automatic deletion occurs.
Back up both the database and archive directory. An external archive is not
automatically added to the existing backup job. Changing the root does not move
existing files or rewrite their recorded paths.

Imports are limited to 250 MB and 500 pages, validated with pypdf, and published
atomically. A duplicate issue returns 409 instead of replacing the PDF beneath
existing markup. The server supports range requests. The reader only renders
nearby pages and caps each canvas width at 2048 pixels to limit iPad memory use.

## Subscriber downloads

The downloader visits
`https://torontostar.pressreader.com/toronto-star/YYYYMMDD/page/1`, opens Options →
Download as PDF, and chooses **Download issue as PDF** (also accepts PressReader's
"in PDF" wording). It confirms the issue action if a second dialog appears. It
never chooses Download page as PDF or prints the web page. It checks the issue
URL and compares the downloaded PDF's page count with the viewer before archiving.

One-time setup, from the checkout running Lunaschal, under the same OS user:

```bash
.venv/bin/pip install -r requirements-pressreader.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m backend.newspapers.pressreader login
```

The last command requires a graphical desktop. Sign in to the Toronto Star in
the opened browser, then return to the terminal and press Enter. The helper
verifies that the subscriber PDF menu is enabled before saving the session.
It does not download an issue during setup. The iPad reader does not need
Playwright or any installed software.

Browser state is saved with mode 0600 at `./data/pressreader/session.json`,
overridable with `PRESSREADER_SESSION_PATH`. Use the same absolute path for the
login helper and server when their working directories differ. This file contains
subscriber cookies and must remain private; do not commit it or paste it into
chat. The password is entered directly into PressReader, not stored by Lunaschal.
For a headless server, run the helper on a graphical machine and transfer the
resulting session file privately to the server path with mode 0600.

Opening Newspapers automatically queues today's issue when a subscriber session
is saved, unless that issue is already archived or downloading. This happens
alongside front-page sync and does not need the daily scheduler setting enabled.
The manual **Download Toronto Star issue** button remains available for older
dates and retries. Settings → General → Newspapers also offers optional daily
downloads after 6 am Toronto time, even when the tab is not opened. This additional
daily schedule is off until enabled. Queued requests and attempt counts persist in SQLite; a restart resumes
interrupted work. One browser download runs at a time, with a four-minute total
deadline. Transient failures retry hourly, at most four attempts per issue, and
the download button explicitly retries an exhausted request. Daily mode queues
the current issue after 06:00 America/Toronto; it does not backfill dates missed
while the server was off. Past issues can be requested with the date picker.

An expired or unentitled session pauses that issue with a reconnect message.
Run the login helper again; the worker notices the new session and resumes paused
requests. A saved session file does not guarantee the subscription is still valid.
Disabling daily downloads stops new automatic requests; already queued requests
continue. Development tests honor `LUNASCHAL_NO_SCHEDULERS`.

Validation covers synthetic PDFs, queue recovery/backoff, local draft recovery,
and a Chromium test of the exact issue-versus-page menu flow with all network
requests intercepted. The live signed-out viewer's Options/PDF menu was checked;
it disables PDF downloads without sign-in. A real authenticated issue download
and real iPad Safari / Apple Pencil interaction remain unverified until setup.
