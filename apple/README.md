# Apple offline client

First native iPhone/iPad slice, targeting iOS/iPadOS 26. The Linux app and
Flask server remain in place. This is source for an initial capture app, not
yet a signed or device-validated release.

The full product scope, staged implementation checklist, and verification status
live in the [Apple/offline implementation tracker](../docs/apple-offline-implementation.md).

## Included

- Offline typed journal entries, plus separate **Transcribe** and **Record**
  captures. Stopping records a journal entry; both modes retain the original
  mono AAC file, and only Transcribe requests server transcription.
- Native Capture / Journal / Library / Settings navigation for iPhone and iPad.
- Durable per-capture manifests in Application Support, replaced atomically;
  audio lives beside them. A separate SQLite replica stores server records,
  full-text search, sync cursors, and revision-checked journal edits.
- Stable client ULIDs, original capture timestamps, sequential retry-safe
  uploads, server acknowledgement validation, and read-back of titles and
  transcripts for the 30 most recent synced captures on this device.
- Authentication through the existing password + display-code login. Only the
  resulting session token is saved, in this device's Keychain. Expired sessions
  pause uploads; offline capture remains usable. The password is not persisted.
- HTTPS only, normally using the existing Tailscale hostname. Redirects are
  refused. Once authenticated, the capture store is bound to that server so a
  settings edit cannot send a backlog to another host. Sign-out removes the
  local session token and retains captures and that binding.
- Cellular text/audio sync enabled by default with a per-device switch. Turning
  it off cancels an in-flight sync; subsequent requests prohibit cellular and
  expensive-network access. Reconnection is retried every 30 seconds while the
  app is active. A capture-specific 4xx rejection requires manual retry;
  network errors, 408, 429 and 5xx remain pending.
- Local audio playback and export. No automatic deletion of original captures,
  even after successful upload. A 404 on a previously synced entry preserves
  the local original and never recreates the server entry.
- Historical journal download, offline text editing/deletion, and explicit
  conflict resolution. Pending text survives server changes and rebootstrap;
  deleted entries can be saved as a separate new capture.
- Library metadata and an explicit Wi-Fi-only chapter/text download. Downloaded
  fics can be searched and read without a server connection. Media files, PDFs,
  download budgets, and per-collection selection are still being implemented.

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
xcodegen generate --spec apple/project.yml
xcodebuild -project apple/Lunaschal.xcodeproj -scheme Lunaschal \
  -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO build
```

The `Lunaschal` scheme also includes an XCUITest that saves a journal entry
without signing in, terminates the app, and verifies that the capture survives
relaunch. Run the scheme's tests on an available iPhone simulator.

`.github/workflows/apple.yml` runs the core tests on Linux and a hosted Mac,
then generates the Xcode project and runs the iPhone simulator test. It requires
no Apple credentials and does not publish anything. It only runs once the
branch is pushed or the workflow is otherwise available on GitHub; creating
the file locally does not run CI.

The bundle ID `com.lunaschal.mobile` is a starting value. Before a signed build,
choose/register the actual bundle ID in the user's Apple team, supply the team
ID, add app icons, and configure distribution signing and App Store Connect
credentials in CI secrets. Then add an explicitly triggered TestFlight release
workflow. No signing credentials belong in project files. The 2015 Monterey
MacBook is not required by this build route.

For development login, the server must run with network-mode authentication
enabled and be reached over its Tailscale HTTPS address. This client requires
an actual session cookie from login; a localhost/auth-bypassed response is not
treated as a successful authenticated connection. Do not expose the server
publicly to make CI work: simulator capture tests never contact it.

## Current limits and next stages

- Uploads use foreground URLSession tasks. They stop when the app leaves the
  foreground and resume from the durable outbox when it returns. This is not
  yet background URLSession transfer or background refresh.
- Audio background mode is declared for an active recording, but lock-screen,
  calls, Bluetooth, interruptions, and Tailscale/cellular transitions still
  require device validation. Normal stops finalize the AAC file. After process
  termination, the manifest is marked interrupted; the audio is retained for
  playback/export and explicit recovery. An AAC container killed before
  finalization may not be playable. We do not claim crash-proof in-flight audio.
- The journal list displays the first 200 server entries plus local captures;
  historical pagination and attachment imports remain outstanding. Library
  search queries the downloaded SQLite records, with up to 200 displayed hits.
- Binary media downloads, YouTube URL capture, share extensions, and PencilKit
  are next stages. Current bulk text downloads prohibit cellular and expensive
  connections; actual Tailscale/hotspot policy needs device validation.
- Apple Watch Series 7 companion is a later target: preserve audio on the watch,
  transfer to the iPhone, then reuse this outbox and server protocol. Watch
  capture must not depend on the server or phone being reachable immediately.
- Local speech recognition and Foundation Models are optional later layers.
  Server transcription is the only transcription path in this first slice.
- Practice and Notebook are intentionally absent from this app's navigation.

Device baseline supplied by the user: iPhone 16 and 2021 12.9-inch M1 iPad Pro
with Pencil 2, both on 26.6.2; Watch Series 7 on 26.6. The app uses APIs available
from iOS 26 and can be built by the hosted toolchain without needing the exact
same patch-level SDK as those devices.
