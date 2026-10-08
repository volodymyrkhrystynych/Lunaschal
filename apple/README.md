# Apple offline client

Native iPhone/iPad offline client, targeting iOS/iPadOS 26. The Linux app and
Flask server remain in place. This is a development implementation, not
yet a signed or device-validated release.

The full product scope, staged implementation checklist, and verification status
live in the [Apple/offline implementation tracker](../docs/apple-offline-implementation.md).
See the [supported-feature matrix and recovery notes](SUPPORTED_FEATURES.md) for
what each device can currently do and which work remains device-only.

## Included

- A PencilKit drawing workspace with fixed A4 coordinates, native tool picker,
  local checkpoints, undo, zoom, and editable-ink/PNG export. Import editable ink
  restores an exported `.drawing` file as a new page, preserving its original
  bytes and all existing pages. Files without editable strokes (including blank
  drawings) and imports above 64 MB are rejected.
  **Save to Paper** explicitly queues an immutable original and PNG for the server;
  ordinary autosave remains local. Downloaded native pages can be opened for
  editing on another Apple device. Linux shows native pages as saved previews;
  existing web strokes remain in their original format.
  Each checkpoint publishes only after its ink and preview are written; the
  current and preceding versions are retained, with explicit recovery of a
  validated previous checkpoint after a load/save error. Library cleanup cannot remove
  drawings. Stale saves retain a conflict copy; they never overwrite newer ink.
  Cross-platform ink conversion and PDF annotation remain outstanding.

- **iPad notebooks** (Capture → **Notes** or **Newspaper**, top right). A
  full-window, paginated PaperKit canvas with the tab bar hidden: Pencil ink,
  pictures that can be moved and resized, text boxes and shapes from the tool
  picker's **+**, and fixed pages (A4 for notes) navigated from the top bar.
  **Newspaper** opens today's archived issue (by the 4am day; the newest is offered if
  today's isn't in) with every PDF page under the ink. It downloads the PDF once
  from `GET /api/newspapers/issues/<date>/pdf`, since mobile sync doesn't carry
  issue PDFs, and reopens the same unsaved notebook instead of making a second one.
  The web reader's own markup is untouched. Back autosaves (current + previous
  checkpoint, like drawings) and the notebook is listed under Draw → Notebooks to
  continue. **Save** files one journal entry: each page as a JPEG (a newspaper
  files its cover plus the pages written on), plus the notebook's one YouTube link.
  The text composer's draft is never touched.
  **Lock pictures on this page** (camera menu) pins a page's pictures under the
  ink so they can be written over but not selected or dragged; a lock badge
  shows beside the page number, and Unlock makes them movable again. PaperKit
  has no per-item lock, so the pictures move to a layer drawn beneath the canvas
  (`page-N.locked` beside the page's markup in each checkpoint).
  **Screenshots of the other app in Split View**: iPadOS lets no app capture
  another app's pixels, so the screenshot is the system's and Lunaschal cuts its
  own window out of it (the larger remaining strip with Stage Manager). With no
  setup: take a screenshot (Pencil corner swipe, or top + volume), choose **Copy
  and Delete**, then **Paste image**. Paste crops only an image exactly the
  screen's pixel size, and only when the part it would cut matches a snapshot of
  Lunaschal's window, so a copied photo, or an older screenshot taken with the
  apps the other way round, goes in whole. For one tap, a shortcut
  _Take Screenshot → Add Screenshot to Lunaschal Notes_ run from AssistiveTouch
  or a Full Keyboard Access command does the same in the app's process (Back Tap
  is iPhone-only); with no notebook open, its screenshot waits for the next one.

- The Capture tab has an **Entry | Daily** switch where its title was. Entry (the
  default) is the composer below; **Daily** logs the day's selfie (front camera),
  body weight and calorie entries. Each is saved on the device first, keyed by the
  4am day it was logged on, and uploads to the Lifestyle routes on the next sync.
  A newer selfie or weight replaces an unsent one for the same day; calorie entries
  carry a device-minted id so a replay is not counted twice. Once reachable, the
  page shows the server's record of today with unsent logs marked waiting.
  Calories take one line, split as the desktop card does it (`CalorieLine`, a port
  of `parseCalorieEntry`): "chicken and rice, ~600" becomes the food and its count.
- **Workout** (the third Capture page) is the desktop's workout log: one set or
  activity per line ("bicep curls 20, 10" in lb, "squats 10" bodyweight, bare
  "20, 10" for the selected pill, "walking 30" minutes), recent-exercise pills,
  and the last four workouts with Rate / location. Lines are checked on the phone
  with the server's rules (`WorkoutEntry`, a port of `quick_entry.parse_entry`),
  saved on the device, and uploaded in order with a device-minted id and the time
  they were logged, so a replay is a no-op and sets done offline still group into
  the workout they belong to. Rating and location need the server.
- Weather. The Entry page shows the conditions now at its top left; tapping them
  shows feels-like (Open-Meteo's apparent temperature: wind chill and humidity),
  wind and gusts ("windy" from 30 km/h sustained or 50 km/h gusts), and whether
  the sun is up. Daily has the full card: now, sun times, hour by hour. The last
  forecast is cached so it shows offline. With location permission, the Capture
  tab takes a fix when shown and sends it to `POST /api/lifestyle/weather/location`,
  so the forecast is for where the phone is.
- Weather on entries. Save entry and Save food entry attach the fix if it is under
  15 minutes old; Save never waits for GPS. The server's entry-weather sweep
  (`backend/weather/entry.py`, woken by each save) looks the weather up for the
  entry's own place and capture hour, so an entry saved offline still gets the
  weather from when it was written. The Journal list shows it on server entries
  (from the replica) and on this device's captures (read back after upload; meals
  through `GET /api/food/<id>`).
- Offline typed journal entries. On the phone, **Transcribe** and **Record**
  add clips to the Capture tab's draft; stopping keeps the clip there, and
  only **Save entry** turns the draft into one entry. Clips upload through the
  recordings route under that entry's id, in recorded order, so the server
  appends Transcribe clips' words after the typed text. Both modes retain the
  original mono AAC file. The draft (text, links, clips, photos, files)
  survives relaunch; a clip cut off by a kill is kept and marked interrupted.
  The Watch still saves each recording as its own entry. Clip uploads use the
  foreground session, not the background recording uploader.
- Photos (camera or library) and arbitrary files attached to an entry,
  staged into the same draft and uploaded after the entry is
  created, each under its own client-minted attachment ID. Passed in
  simulator for the library picker; the camera needs a device.
- **Save food entry** (bottom left, beside **Save entry** on the right) files
  the same draft in the food log instead: text, photos/videos and clips, under
  client-minted meal and media IDs, with the capture time the server now keeps.
  YouTube links stay in the composer for the next journal entry, and the
  button is disabled while a non-media file is attached. Clips go through the
  food recordings route, which transcribes each one into the meal's note.
  Passed in simulator offline; uploading to a real server is untested.
- Chat works like the desktop's: today's one conversation, the streamed reply
  with its steps and reasoning, sources, Markdown, New chat / Clean slate, the
  delegate's editable confirm cards (calendar, calories, food, recipe, recipe
  link, flashcards), "flashcard this" drafts, the day's to-do bar (tick, rename,
  dismiss, send to the permanent list), and photos. Typing and photos need the
  server; a voice message doesn't: stopping the recording queues the clip
  (with any typed words and staged photos) in the sync outbox, and the server
  transcribes and answers it when it lands. A reply that outlives its stream is
  picked up by polling, as on the desktop. Passed in simulator, offline and
  against a local test server with a stand-in model; not yet verified on device.
- Todo is the desktop Lifestyle tab's tasks card: up to four daily tasks (tick
  for today, add, rename, reorder and delete under Edit) above the To-Do and
  Archive lists, ordered and filtered as on the desktop (soonest due first,
  then priority; a repeating to-do hides until it's near due). A to-do opens
  in a form for title, notes, due date, repeat, priority and list; swipe to
  archive or delete. The tab's red badge counts open To-Do items due today or
  overdue (4am day; archived ones and daily tasks don't count). Every change
  works offline: it shows at once and waits in a sync outbox, sent in order on
  the next pass. A daily-task tick carries the 4am day it was made on, so one
  sent after the rollover still counts for that day, and a daily task created
  offline carries its own id, so a resend can't add it twice. A change the
  server turns down (a fifth daily task, say) is dropped and said in the tab.
  Passed in simulator offline (including the badge and a relaunch with
  changes waiting); not yet run against a server or verified on device.
- Offline YouTube links attached to a typed entry (any number per entry), with
  preserved drafts and stable entry and per-link attachment IDs. Entry creation
  precedes link import; retry validates every acknowledgement. The server keeps the original capture timestamp and
  reuses its existing YouTube import pipeline.
- Native Capture / Journal / Chat / Todo / More tabs on iPhone; iPad also has
  Study and Draw. Journal's toolbar has Sync on the left and a Journal/Calendar
  switch on the right. The Journal page is the desktop's feed: one newest-first timeline
  with inline photos, playable voice clips (desktop WebM clips arrive as an AAC copy),
  videos and YouTube posters, and calendar events' category-coloured borders around the
  entries written during them. Media not already downloaded is fetched and cached when
  online. Calendar is the web's 4am-to-4am day view over the synced events,
  with the web's create, edit and delete (including "This and future" / "All events" on a
  repeating event; Delete is inside Edit), the six category checkboxes on the event's page and
  their colours, overlapping events side by side,
  drag to move (or, with the bottom-left toggle on Length, to change the end), and shaded
  wake/sleep bands with an editor; changes are saved on the device and replayed in order on the next sync. More
  holds Library and Settings; the workout log is Capture → Workout. Library opens directly to books and has a
  Library/Folders switch. Library mode has provider pills and sorts by the site's latest
  chapter date (`latest_activity`), not the download time. Folders mode lists folders and Unsorted,
  and each pushes its books with a Back button. Both have title/tag search, tag and
  Favorite/Continue-reading filters, and recent/title sorting. Other saved material is grouped
  under Study. Download controls remain in More → Settings → Library downloads.
  Tapping a book opens the reader at its resume point (`ReplicaStore.resumePoint`:
  continue bookmark, then this device's last read, then the server's, then chapter 1),
  and the reader moves between chapters with Previous/Next. The reader's bottom-left menu has Text and
  Transcribe commentary (a journal capture carrying `ficID`/`chapterID`: typed text
  is posted as `raw_content` and then linked, a recording links in its upload),
  and Continue and Bookmark. User scrolling is logged as reading spans
  (`ReadingSpans.swift`, a port of `src/lib/readingSpans.ts`), and opening a
  chapter queues it as last read. Both go through `FicActivityStore`. Chapter
  readers create Favorite or Continue-reading bookmarks offline, and
  saved bookmarks can be reopened or removed from a book. Bookmark changes sync
  with the desktop, using replay receipts and conflict checks. One pending
  Continue change per book is retained until it syncs or is resolved.
  Native drawing editing is iPad-only.
- Study (iPad-only) contains Documents, Paper previews, newspapers, and Knowledge.
  The iPhone has no Study tab, so none of these are reachable there.
  On iPad, downloaded PDFs and images
  open with Pencil-only annotation, finger pan/zoom, undo, page navigation, and
  per-page ink autosave. There is no Notebook/text editor. Originals remain
  unchanged; ink lives outside download cleanup and is isolated by source ID,
  file version, and page. Export an annotated page as PNG or its editable ink.
  Study annotations currently stay on the iPad and do not sync to the server.
  Replacing a source file preserves the old ink on disk but does not apply it
  to the new document; browsing old-version ink is not yet exposed. Archived
  HTML and video sources retain their readers without annotation.
- Durable per-capture manifests in Application Support, replaced atomically;
  audio lives beside them. A separate SQLite replica stores server records,
  full-text search, sync cursors, and revision-checked journal edits.
- Stable client ULIDs, original capture timestamps, sequential retry-safe
  uploads, server acknowledgement validation, and read-back of titles and
  transcripts for the 30 most recent synced captures on this device.
- Persistent recording upload bodies with destination/identity checks and
  SHA-256 verification. Retries reuse the same multipart file and boundary;
  missing or damaged staging is rebuilt from retained audio before sending.
  Staging is removed only after the synced capture state is saved, including
  cleanup recovery after relaunch. Original audio is never removed by this path.
- Authentication through the existing password + display-code login. Only the
  resulting session token is saved, in this device's Keychain. Expired sessions
  pause uploads; offline capture remains usable. The password is not persisted.
- HTTPS only, normally using the existing Tailscale hostname. Redirects are
  refused. Once authenticated, the capture store is bound to that server so a
  settings edit cannot send a backlog to another host. Sign-out removes the
  local session token and retains captures and that binding.
- Cellular text/audio sync enabled by default with a per-device switch. Turning
  it off cancels an in-flight sync; subsequent requests prohibit cellular and
  expensive-network access. The app checks for work every 30 seconds while
  active. Capture upload failures use persisted exponential backoff from 30
  seconds to 30 minutes; Sync retries waiting uploads immediately. Authentication
  failures pause uploads until login, and a capture-specific 4xx rejection
  requires Retry upload. Interrupted foreground attempts recover at the next
  sync with new attempt identities; obsolete completions cannot acknowledge them.
  Cancellation keeps captures pending without increasing failure backoff.
- Local audio playback and export. No automatic deletion of original captures,
  even after successful upload. A 404 on a previously synced entry preserves
  the local original and never recreates the server entry.
- Historical journal download, offline text editing/deletion, and explicit
  conflict resolution. Pending text survives server changes and rebootstrap;
  deleted entries can be saved as a separate new capture.
- Library metadata and explicit Wi-Fi-only text/media downloads. Downloaded
  fics can be searched and read without a server connection. PDF books, journal attachments,
  Study documents, Paper previews/pictures, and newspaper covers have selectable
  media downloads, a 20 GB default media budget, and resumable 1 MB range reads.
  SHA-256 verification precedes availability; original captures live separately.
  PDF books open in PDFKit. The client checks server media capabilities before
  downloading; older servers continue serving their supported collections and
  display an update notice for PDF-book support.
  Bulk database and file work runs on a separate actor and SQLite connection;
  you can browse and capture while downloads run, including after switching tabs.
  After a completed Wi-Fi text download, ordinary sync also fetches incremental
  chapter and reading-content changes using the cellular preference. These passes
  are bounded to five pages and never fetch binary media or start a bootstrap.
  Expired cursors and incomplete first downloads wait for the next Wi-Fi download.
  Existing installations need one Wi-Fi download pass to establish this checkpoint.
- Local PDF, image, audio/video, and archived-article views. Articles use a
  script-disabled WebKit view with remote resources blocked. Knowledge article
  text is opt-in. Archive videos and ZIM packages are not bulk-downloaded.
- Downloaded Knowledge articles are browsable and searchable in Library, with
  Load more support and a selectable plain-text reader. Turning off future
  Knowledge downloads keeps existing articles readable. Markdown source is
  shown as text; remote images and embedded pages are not loaded. This reads
  replicated Knowledge articles, not Wikipedia ZIM packages.
- Paper documents and newspaper covers have searchable Library sections. Paper
  previews open in page order without modifying server ink. Text chapters and PDF
  documents keep device-local reading positions tied to their content version.
- Capture details keep typed originals, server raw text, the matching recording
  transcript, and the current journal entry distinct. Historical entries expose
  their original text and individual attachment transcripts.

The backend accepts optional offset-bearing ISO `capturedAt` on both
`POST /api/journal` and `POST /api/journal/recordings`. On recording uploads it
dates the entry and the attachment. Missing values preserve existing browser
behavior; invalid values are rejected before creating an entry. Retry does not
change the original capture time. Install the accompanying backend change
before using the native client, or an older server will date late uploads by
arrival time.

## Build and verification

Pure persistence, request-format and retry tests run on Swift 6.2 on Linux or Mac:

```sh
swift test --package-path apple/LunaschalCore
```

On a Mac with Xcode 26 or newer and XcodeGen:

```sh
swift apple/tools/make_icons.swift
xcodegen generate --spec apple/project.yml
xcodebuild -project apple/Lunaschal.xcodeproj -scheme Lunaschal \
  -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO build
```

The `Lunaschal` scheme includes XCUITests for journal capture/search and drawing-page
creation without signing in, terminating the app, and reopening the saved work.
It also includes four native drawing-import tests and two PDF download/reading-position
tests. [Verification at `87c7924`](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/37136162108)
passed all eight native tests, 93 Linux / 94 macOS core tests, four signing-helper
tests, Watch compilation, and unsigned device archive inspection in Xcode 26.6.
Run the scheme's tests on an iPhone simulator. Files-provider interaction and
Pencil hardware behavior still need device verification.

`.github/workflows/apple.yml` runs the core tests on Linux and a hosted Mac,
then generates the Xcode project and runs the iPhone simulator test. It requires
no Apple credentials and does not publish anything. It only runs once the
branch is pushed or the workflow is otherwise available on GitHub; creating
the file locally does not run CI.

The bundle ID `com.lunaschal.mobile` is a starting value. Before a signed build,
choose/register the actual bundle ID in the user's Apple team, supply the team
ID, review the generated app icons, and configure distribution signing and App
Store Connect credentials in the release environment. The manual release
workflow exports by default and uploads only when explicitly selected; see
[signing and first installation](SIGNING.md). No signing credentials belong in project files. The 2015 Monterey
MacBook is not required by this build route.

For development login, the server must run with network-mode authentication
enabled and be reached over its Tailscale HTTPS address. This client requires
an actual session cookie from login; a localhost/auth-bypassed response is not
treated as a successful authenticated connection. Do not expose the server
publicly to make CI work: simulator capture tests never contact it.

## Current limits and next stages

- Uploads use an ephemeral URLSession while active or during an iOS-granted
  `BGProcessingTask` window. Background sync can be disabled in Settings;
  iOS chooses when to run it. Expiration cancels work and preserves pending
  captures for retry. Ordinary foreground work stops when leaving the app.
  This is not autonomous background URLSession transfer: bytes do not continue
  after process termination. The existing redirect and cellular checks apply;
  bulk media downloads remain a separate Wi-Fi-only action. Persistent request
  staging currently covers recordings; text and YouTube request bodies are
  rebuilt from capture manifests. Staged recordings use additional device space
  outside the downloaded-library media budget.
- Audio background mode is declared for an active recording, but lock-screen,
  calls, Bluetooth, interruptions, and Tailscale/cellular transitions still
  require device validation. Normal stops finalize the AAC file. After process
  termination, the manifest is marked interrupted; the audio is retained for
  playback/export and explicit recovery. An AAC container killed before
  finalization may not be playable. We do not claim crash-proof in-flight audio.
- Journal initially shows 200 records; each Library category starts with 50, with Load more
  controls to reveal further records already stored on the device. Library search
  filters the selected category and can expand beyond 50 matches. Counts and
  results share the same filter and stable revision/ID ordering. Loading more
  does not contact the server. Attachment imports remain outstanding.
- Downloads continue across tabs while the app is active; leaving the app pauses
  them. Partial files resume
  on the next download request. Selection changes retain existing copies;
  “Remove downloaded media” explicitly clears media copies and partials, while
  retaining capture originals and server records. Individual downloaded files
  can also be removed from their reader. Shared bytes remain until no other
  downloaded record references them; unreadable manifests block removal safely.
  Partial downloads and old content versions remain until whole-media cleanup.
  Future bulk downloads can restore a removed item. Pinning is not implemented.
  Settings → Library downloads shows current media-directory usage, including partial downloads.
  File readers distinguish metadata-only, pending, partial, downloaded, and
  server-unavailable states. Server observations persist across relaunch and are
  labelled as the last check; they never hide an existing verified local copy.
  Complete but unverified partial files remain pending verification. These states
  refresh when a library download starts or finishes, including pause/failure.
- Inline chapter images, full newspaper PDFs,
  share extensions, and Paper drawing sync remain outstanding. Bulk requests prohibit
  cellular and expensive connections; actual Tailscale/hotspot policy needs
  device validation. The budget currently covers media, not SQLite text.
- The Watch target now has Record / Transcribe / Stop, persistent recordings,
  interrupted-file recovery, and queued WatchConnectivity handoff. The phone
  copies incoming temporary files synchronously, verifies their hashes, and
  imports them without changing their IDs, times, modes, or prior upload state.
  “Saved on phone” is a separate durable receipt, not a server-upload claim.
  A separate durable server-upload receipt enables confirmed removal of the Watch
  copy. The phone's original is retained. Upload receipt does not mean transcription
  finished or a backup exists. No automatic cleanup occurs. Both receipt stages
  survive restarts, and older imports can request server status without resending audio.
  Watch and iPhone simulator compilation passed in the hosted Xcode 26.6 run.
  Recording lifecycle and paired-device transfers remain unverified.
  WatchConnectivity transfer validation requires paired devices
  ([Apple's transferFile documentation](<https://developer.apple.com/documentation/watchconnectivity/wcsession/transferfile(_:metadata:)>)).
- The Watch has a pomodoro timer: Focus (25 minutes, then Continue, a 5-minute
  Break, or Cancel) and Timeout (10 minutes, then Continue or Cancel). Rules are
  `PomodoroTimer` in LunaschalCore; the state is saved, so a relaunch resumes from
  the end time. A local notification carries the same buttons, since the app is
  suspended with the wrist down. Each finished or cancelled run goes to the phone
  by `transferUserInfo` and is deleted from the Watch only after the phone replies
  `pomodoroStored`; the phone's `pomodoro-outbox` uploads it to
  `POST /api/lifestyle/pomodoro/sessions`, and Lifestyle shows it on the Focus card.
  A cancel within the first minute is not logged. Debug builds take
  `-PomodoroSeconds 10` and `-PomodoroStart work|timeout` launch arguments for
  checking the simulator without tapping. The countdown, end-of-timer choices and
  relaunch passed in the watchOS simulator; notification buttons and the
  Watch-to-phone transfer need paired devices.
- Watch complications and Controls (`apple/WatchWidgets`, a WidgetKit
  extension embedded in the Watch app, plus `apple/WatchShared`, compiled into
  both). **A complication on the face cannot act without opening the app** —
  watchOS has no interactive complications — so each one opens it and does
  its job at once: **Focus 25** and **Timeout 10** start their timer (pressed
  again while it runs, or while it waits for Continue / Break, they only show
  the timer: `PomodoroTimer.press`), and **Record** and **Transcribe** start a
  clip, or stop and save the one going. **Pomodoro** shows the run (a ring,
  corner, inline or long), and **Lunaschal** is a row of all four buttons for
  the long slot on the Modular faces, each its own `Link`. Taps arrive as
  `lunaschal-watch://` URLs (`WatchLink`) through `onOpenURL`. **Controls** —
  Control Center, the Smart Stack, the Action button — are the one place a
  press acts without opening the app, so the same four exist there: Focus and
  Timeout (`StartTimerIntent`; a second press calls `continueInForeground` and
  leaves a pending link so the app opens on the timer) and Record and
  Transcribe as toggles (`ToggleRecordingIntent`, an `AudioRecordingIntent`
  so it runs in the app's process, where the recorder and the clips are). A
  Control's timer intent may run in the extension's process with the app
  asleep, so the timer's state, its outbox and the end-of-run notification
  live in `PomodoroEngine`, over the `group.com.lunaschal.mobile.watch` App
  Group container; a timer kept in the app's own container by an earlier
  build moves there once. The app writes `recording.json` there for the
  recording buttons. Without the App Group (an unsigned build) everything falls
  back to the app's own container and the face shows idle glyphs. Debug builds
  take `-WatchLink <url>[,<url>…]` (taps three seconds apart) since the watchOS
  simulator cannot open URLs. Passed in the simulator: the move into the App
  Group, the first and second Focus press, Transcribe on then off saving a
  pending clip, and the system finding all four Controls. Needs a device: how
  they look, whether the launcher's links are separately tappable on a face,
  whether a Control's notification and recording start with the app asleep
  (the recording toggle may be refused by watchOS without the app in front).
- Settings checks the actual Foundation Models text-model and locale availability,
  including disabled/ineligible/not-ready states. Generation and local speech
  recognition remain optional later layers. Server transcription is the only
  implemented transcription path.
- Practice and Notebook are intentionally absent from this app's navigation.

Device baseline supplied by the user: iPhone 16 and 2021 12.9-inch M1 iPad Pro
with Pencil 2, both on 26.6.2; Watch Series 7 on 26.6. The app uses APIs available
from iOS 26 and can be built by the hosted toolchain without needing the exact
same patch-level SDK as those devices.

Media-store Linux tests inject a verifier to exercise publication/recovery;
production hashing uses Apple CryptoKit. A known SHA-256 fixture runs on the
hosted Mac. Linux verification does not establish Apple framework correctness.
