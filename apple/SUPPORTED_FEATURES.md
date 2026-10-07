# Supported experience and recovery

This describes the current development branch. There is no signed
device release yet. Hosted builds verify compilation, persistence, device archive
structure, and selected simulator behaviors; none of the user's physical devices
has been validated. Exact commits and CI results are recorded in the
[implementation tracker](../docs/apple-offline-implementation.md).

| Feature                             | iPhone and iPad                                                                                                                                                                                                                                                         | Watch Series 7                                    | Connection needed                                                                                     |
| ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Typed journal capture               | Saved locally with unfinished draft                                                                                                                                                                                                                                     | Not exposed                                       | Upload later                                                                                          |
| Weather                             | Current conditions on Entry and Daily; each entry's weather in Journal; location fix attached on Save                                                                                                                                                                   | Not exposed                                       | Server for the forecast and the per-entry lookup                                                      |
| Workout log                         | Capture → Workout; sets saved locally and grouped by when they were logged; Rate / location online                                                                                                                                                                      | Not exposed                                       | Upload later; rating needs the server                                                                 |
| Daily selfie, body weight, calories | Capture → Daily; saved locally, filed under the 4am day it was logged on; today's server record shown when reachable                                                                                                                                                    | Not exposed                                       | Upload later                                                                                          |
| Record / Transcribe                 | Clips staged in the entry draft until Save; original audio retained; server transcribes                                                                                                                                                                                 | Durable recording and queued phone handoff        | Phone for handoff; server for upload/transcription                                                    |
| Food log entry                      | Save food entry: text, photos/videos, clips; staged offline, uploaded later                                                                                                                                                                                             | Not exposed                                       | Server for structuring/transcription                                                                  |
| Photos and files on an entry        | Camera, photo library, Files; staged offline and uploaded after the entry                                                                                                                                                                                               | Not exposed                                       | Server for captions/import                                                                            |
| YouTube links on an entry           | Offline capture, several per entry; metadata/import queued                                                                                                                                                                                                              | Not exposed                                       | Server for import; archived video is not downloaded                                                   |
| Historical Journal                  | Download, search, text edits/deletion, explicit conflicts; desktop-style feed with inline photos, audio, video/YouTube and calendar-event borders                                                                                                                       | Not exposed                                       | Sync; downloaded entries read offline                                                                 |
| Books and Study documents           | Text chapters, PDFs, stored articles, image/audio/video readers                                                                                                                                                                                                         | Not exposed                                       | Explicit Wi-Fi download before offline reading                                                        |
| Reading position                    | Device-local paragraph/PDF page, Continue chapter                                                                                                                                                                                                                       | Not exposed                                       | None after download; no cross-device position sync                                                    |
| Paper documents                     | Searchable ordered previews; downloaded native pages reopen for editing                                                                                                                                                                                                 | Not exposed                                       | Wi-Fi download of original and preview                                                                |
| Native drawing                      | A4 PencilKit, local checkpoints, explicit Save to Paper, conflict copies, ink import/export                                                                                                                                                                             | Not exposed                                       | Local work offline; queued saves need server                                                          |
| Newspapers                          | Searchable downloaded front-page images                                                                                                                                                                                                                                 | Not exposed                                       | Wi-Fi download; full PDFs/annotations remain outstanding                                              |
| Knowledge                           | Optional downloaded article text and search                                                                                                                                                                                                                             | Not exposed                                       | Wi-Fi download; no ZIM reader                                                                         |
| Background uploads                  | Opportunistic processing windows with durable retry state                                                                                                                                                                                                               | WatchConnectivity transfer queue                  | OS scheduling, reachable phone/server                                                                 |
| Storage cleanup                     | Confirmed removal of downloaded copies; originals retained                                                                                                                                                                                                              | Confirmed Watch-copy removal after server receipt | Receipt must arrive before Watch removal                                                              |
| Pomodoro timer                      | Queues Watch runs for the server; shown on the web Lifestyle tab                                                                                                                                                                                                        | Focus 25/5 and Timeout 10, notification buttons   | Paired phone to log; timer itself works offline                                                       |
| Apple Intelligence                  | Runtime text-model/locale availability check                                                                                                                                                                                                                            | Not exposed                                       | No generation or local transcription implemented                                                      |
| Share extension                     | Not implemented                                                                                                                                                                                                                                                         | Not applicable                                    | —                                                                                                     |
| Chat                                | Today's conversation, streamed replies, confirm cards, to-do bar, photos, voice messages                                                                                                                                                                                | Not exposed                                       | Server for typing/photos/cards; voice messages queue offline                                          |
| Todo                                | Daily tasks and To-Do/Archive lists; badge for to-dos due today or overdue                                                                                                                                                                                              | Not exposed                                       | None; changes queue offline and sync in order                                                         |
| Jobs                                | More → Jobs: the desktop's triage feed (Worth a look / The rest, Best match or Nearest), Queue and Dismiss                                                                                                                                                              | Not exposed                                       | Server to load new postings; last feed shown offline, decisions queue offline                         |
| Calendar                            | Journal → Calendar switch: the web's phone day view (4am–4am timeline); view, create, edit, delete and drag events offline (bottom-left toggle: move or change length); category checkboxes and colours; wake/sleep bands                                               | Not exposed                                       | Sync; changes queue offline and replay in order                                                       |
| Learning                            | More → Learning: Review (typed or mic-dictated answers graded by the server, Again/Hard/Good/Easy, resumes where left), Queue (approve, regenerate, deny), Browse (edit wording/tags, delete); folder and tag filter; More badge for due cards; speech mode in Settings | Not exposed                                       | Server for everything; no offline queue. No card chat, verification, brain-dump or folders management |
| Food, Lifestyle                     | Use the existing web app; native Food is Save food entry only, native Lifestyle is Capture → Daily only                                                                                                                                                                 | Not exposed                                       | Existing web app requirements apply                                                                   |
| Practice and Notebook               | Intentionally omitted from native navigation                                                                                                                                                                                                                            | Not exposed                                       | Existing Linux/web app remains available                                                              |

The iPhone tabs are Capture, Journal, Chat, Todo, and More. More holds Library,
Jobs, Learning and Settings; the workout log is Capture → Workout. Chat is the desktop's Chat
tab, and Todo is the tasks card from the desktop's Lifestyle tab (see README.md). iPad also has Study and Draw, and hides the tab bar while a drawing,
a notebook (Capture → Notes / Newspaper) or a Study annotation is open, so its toolbar doesn't overflow;
native drawing creation/editing is iPad-only, and so are Paper previews, since they live under Study.
A notes notebook turns pages with a finger swipe (with a **+ New page** marker past the last page) and a
newspaper notebook is one continuous scroll fitted to the width; both passed in simulator
(`testNotesPagesTurnWithASwipe`, `testNewspaperScrollsAsOneColumn`, portrait and landscape), and neither
has been verified on device — in particular how a finger and a Pencil share the page.
Library is books-only and switches between two views. Library picks a provider
(site or file type) and sorts by the site's latest chapter date. Folders lists
the desktop's folders plus Unsorted, and each opens with a Back button. Both have
title/tag search, tag/bookmark filters and recent/title sorting. Opening a book
resumes it the way the desktop does (continue bookmark, then last read, then the
first chapter). The reader's bottom-left menu takes typed or spoken commentary
(a journal entry linked to the chapter) and Continue/Favorite bookmarks. Scrolling
is logged as reading spans, and the chapter is reported as last read; both are
queued offline.
Study owns Documents and other saved reference material, and is iPad-only.
On iPad it provides Pencil annotations for downloaded PDFs
and images without a Notebook editor. Ink autosaves locally and can be exported
per page as editable ink or a flattened PNG. It does not yet sync to the server;
source versions have separate ink, and old-version browsing is not exposed.
HTML/video sources remain read-only.
Library download controls live under More → Settings → Library downloads. Bulk downloads
stay on Wi-Fi and continue while switching tabs. Subsequent chapter/text updates
follow the cellular preference; full resyncs and binary media remain Wi-Fi-only.

Opening a fic that isn't on the device downloads it on its own, at the front of a
queue and over any connection (opening a book is asking for it): its chapters through
`GET /api/mobile/fics/<id>/download`, or its PDF in 1 MB ranges. A running bulk download
pauses for it and resumes once the queue is empty. The library list and the book page
show the fic downloading now, its progress, and the time left estimated from this
session's transfer rate. A restarted bulk bootstrap no longer empties already-downloaded
chapters; records the server no longer has are swept when the bootstrap finishes.
Passed in simulator and in `swift test`; not yet verified on device.
There is
no dedicated iPad split-view navigation yet. The same app serves phone and iPad;
Pencil drawing is intended for the iPad and still needs Pencil 2 hardware testing.
The Watch only depends on the paired phone for handoff, not direct Tailscale access.

## Before replacing or removing the app

- Keep the same bundle identifiers and Apple team for upgrades. Installation
  and data preservation across real signed upgrades have not yet been tested.
- Upload pending captures when possible and check the resulting server entries.
  Watch “Saved on phone” is not a server receipt. “Uploaded to server” does not
  mean transcription is finished or a server backup exists.
- Export device-only recordings from their capture details. Export each native
  drawing's editable ink as well as its PNG if a portable preview is useful.
  Editable ink can be imported as a new page; PNG does not restore editable strokes.
- Typed originals are selectable in capture details. Bulk capture backup and
  restore are not implemented. Downloaded library copies can be fetched again,
  but unsynced captures, local drawings, and pending edits cannot be reconstructed
  from the server. Do not treat reinstalling the app as a troubleshooting step
  while it holds the only copy of work.

## Recovering common failures

| Failure                                         | Existing recovery path                                                              | Limit                                                                       |
| ----------------------------------------------- | ----------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Server/Tailscale unavailable                    | Capture locally; reconnect and retry sync                                           | Bulk download cannot finish without the server                              |
| Expired login                                   | Sign in again to the same server; pending captures remain                           | Changing server binding/reset is not implemented                            |
| Upload interrupted or response lost             | Durable retry identities and server idempotency                                     | Foreground transfer bytes do not continue after process termination         |
| Recording interrupted                           | Play/export retained audio; explicitly keep it if playable                          | An unfinalized AAC container may be unplayable                              |
| Drawing checkpoint fails                        | Keep current/previous generations; restore previous saved version                   | Checkpoints are local and are not a server backup                           |
| Notebook checkpoint or newspaper download fails | Previous checkpoint kept and restorable; a failed PDF download creates no notebook  | Notebooks are local until Save files them as a journal entry                |
| Download fails or storage fills                 | Pause/retry; verified range downloads resume; remove downloaded copies              | No automatic eviction; text and staged uploads are outside the media budget |
| Concurrent Journal edits                        | Review conflict; apply against latest revision or save text separately              | Do not discard a pending change unless it is no longer needed               |
| Concurrent drawing edits                        | Keep the queued original; create a new local copy and explicitly Save it to Paper   | Native ink never merges automatically; web ink is preview-only on Apple     |
| Watch receipt replay                            | Durable receipt and matching identities prevent duplicate capture/removal           | Physical pairing/background delivery still needs validation                 |
| Server restored from backup                     | Authorized operator rotates sync epoch, then devices bootstrap and review conflicts | No automatic server-restore detector                                        |
| Older app cannot open replica                   | Install a compatible fixed build that understands its schema                        | Do not delete the replica: it contains pending edits and reading positions  |

Current replica schema is version 3. Its migrations update existing newspaper
search titles and journal original-text indexes without redownloading records or
losing cursors/pending edits. Rolling back to a
binary that accepts only version 1 or 2 fails closed; a compatible replacement build
is the recovery path. Capture manifests remain separate from the SQLite replica.

Drawing Save requires a configured server and one successful initial sync. New
drawings create one-page Paper documents; later saves update ink and preview,
preserving the server's title and Journal filing flag. Local rename currently
does not rename an already published Paper. Downloads never replace a local
draft or queued save. Opening a newer downloaded version refreshes only a clean,
acknowledged native copy. Drawing conflicts and old server epochs require keeping
a separate copy; there is no automatic merge or force-overwrite action.

## Release boundary

Use [SIGNING.md](SIGNING.md) for Team ID, profiles, secrets, the exact-tested-commit
release gate, and explicit upload selection. No release workflow has been run
with real credentials. A hosted unsigned archive is not installable on a device.
The outstanding release checks include microphone/Pencil behavior, paired Watch
delivery, long recordings, disk pressure, cellular/Tailscale transitions,
accessibility, App Store metadata, and actual signed upgrade/recovery behavior.
