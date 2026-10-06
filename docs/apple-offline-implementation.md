# Apple apps and offline experience — implementation tracker

Last updated: 2026-10-04. Working branch: `fix/apple-library-navigation`.

Books-library follow-up:

- Library now opens directly to books. Title/tag search, source/folder/tag
  filtering, Unsorted, recent/latest/title ordering, and Favorite/Continue
  bookmark filters work over the offline replica with consistent pagination.
- Safe book snapshots now include folders/tags/source/latest chapter activity;
  related-row triggers keep changes and removals in the immutable sync feed.
  This projection change requires a server update and fresh mobile bootstrap.
- Chapter bookmarks queue offline and sync to the desktop table through durable
  receipts and revision checks. Pending/conflicting changes are visible in the
  book; a pending Continue change must finish before another replaces it.
- Study holds non-book material. The tab is iPad-only; the iPhone no longer shows it
  (UI test asserts its absence on iPhone).
- Local verification: 110 Swift core tests and 54 backend bookmark/sync/seeding
  tests passed. Hosted Apple validation is pending.
  The earlier Study run passed native annotation/iPhone tests but failed iPad
  tab selectors; selectors now support floating iPad tabs.

iPad Study follow-up:

- Added an iPad-only Study tab and moved its Documents list out of Library.
  iPhone retains Documents under Library.
- Downloaded PDF/image pages support Pencil annotation, finger pan/zoom, undo,
  autosave, previous-checkpoint recovery, and annotated PNG/editable-ink export.
  There is no Notebook editor. Web pages/videos retain existing readers.
- Study ink is local-only, separate from download cleanup and keyed by source
  ID, file hash, and page. Source originals are untouched; a replaced source
  cannot inherit old marks. Server sync and old-version ink browsing remain open.
- 105 portable Swift tests passed. Three native annotation tests cover reopen,
  page transitions, erasure, original-file preservation, export, and load failure.
  Hosted Apple verification is pending.

Library usability follow-up: replaced the combined scrolling list with five
category destinations, each searchable with 50-item pages. Moved download
selection, progress/pause, storage budget, and cleanup into Settings → Library
downloads, retaining existing preferences. Added a simulator regression test
for reaching every category and finding download controls under Settings.
Library navigation and exempt-encryption declarations passed hosted Apple CI at
`0562fda`. Additional device/download follow-up:

- Draw and native-page editing are iPad-only; iPhone keeps Paper previews.
- Bulk downloads remain Wi-Fi-only and run database/file work on a worker actor,
  with an independent SQLite connection and a model-owned cancellable task.
  Switching tabs keeps the task alive; leaving the app pauses it.
- Completed text downloads receive bounded incremental chapter/reading-content
  updates through ordinary sync, honoring the cellular preference. Cold,
  incomplete, or expired scopes require Wi-Fi; media stays bulk-only.
- Added worker/cursor/cancellation tests and an iPad drawing simulator check.
  Local Swift core verification passed (102 tests), including rejection of stale
  responses after cursor resets; hosted Apple checks are pending.

This is the implementation plan and progress tracker for Lunaschal on iPhone,
iPad, and Apple Watch. It records the agreed product direction, the first
implementation, and the work needed to reach the complete offline experience.
Use [apple/README.md](../apple/README.md) for current build instructions and
implementation limits. Keep this document current as work lands.

## Current position

**Native offline capture, journal editing/search/conflicts, selected library
downloads/readers, native PencilKit Paper sync, and Watch handoff/server receipts
are implemented. This is not yet an installable, signed, or device-validated
release.** A manual signing/export workflow, generated icons, privacy manifests,
device archive checks, and a [feature/recovery guide](../apple/SUPPORTED_FEATURES.md)
are now included. Capture foundation: `e018be9`; replica/journal sync: `e695b5e`. Further work is being committed
in stages. The branch was pushed on 2026-09-28; the first
[hosted Apple build](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/36502751471)
passed against `33a694d`: Xcode 26.6 (17F113), Watch simulator build, iPhone
simulator build and offline capture/relaunch test, 33 Linux core tests, and 34
Mac core tests including CryptoKit. The [follow-up build](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/36503732880)
passed against `e71830d`: 35 Linux and 36 Mac core tests, Watch compilation,
and both journal and drawing offline relaunch UI tests.
Drawing restoration is also verified at `1e8b624` in the
[drawing restoration build](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/36558448616):
37 Linux / 38 Mac core tests, four native drawing-import tests, both relaunch
tests, and the Watch simulator build passed.
Persistent recording upload staging is verified at `96765d8` in
[hosted CI](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/36647550695):
43 Linux / 44 Mac core tests, four native drawing tests, two relaunch tests, and
Watch compilation passed. Retry/recovery commit `87568c0` also passed
[hosted CI](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/36649976298):
53 Linux and 54 macOS core tests, six native tests, and Watch compilation.
Opportunistic background processing (`4d9abc3`) passed
[hosted CI](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/36707854242):
60 Linux / 61 macOS core tests, four native drawing tests, two iPhone relaunch
tests, and Watch compilation. Physical-device background delivery remains unverified.

PDF-book downloads (`de29953`) passed
[hosted CI](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/36786909030):
62 Linux / 63 macOS core tests, five native drawing/PDF tests, two iPhone relaunch
tests, and Watch compilation. Local backend media/sync regression: 53 passed.

Individual media-copy removal (`d4d8ee2`) passed
[hosted CI](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/36797973089):
68 Linux / 69 macOS core tests, seven native regression tests, and Watch compilation.

Persistent file availability and corrupt-partial budget recovery (`80ea01c`) passed
[hosted CI](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/36800042127):
73 Linux / 74 macOS core tests, seven native regression tests, and Watch compilation.

Offline browsing beyond 200 records (`0fe5f32`) passed
[hosted CI](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/36852919670):
75 Linux / 76 macOS core tests, seven native regression tests, and Watch compilation.
The Knowledge reader (`e4b5ffd`) then passed
[hosted CI](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/37075943078):
76 Linux / 77 macOS core tests, seven native regression tests, and Watch compilation.

The October 3 continuation added Paper/newspaper browsing, separate transcript
provenance, local chapter/PDF positions, Watch server receipts and confirmed local
removal, and a runtime Apple Intelligence availability check. Verification at
`228dec4` passed 86 Linux / 87 macOS core tests, six drawing/PDF tests, two offline
relaunch tests, Watch compilation, four release-validation tests, and device
archive inspection
([run](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/37096771031)).
The subsequent original-text Journal search/migration at `ef78e21` passed
[hosted verification](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/37097464982):
88 Linux / 89 macOS core tests, six native drawing/PDF tests, two offline
relaunch tests (including matching and non-matching Journal searches), four
release-validation tests, Watch compilation, and inspected device archive.
The merged backend
baseline passed 57 media, sync, and seeder tests. No production deployment or
signed release has run.

Native Paper sync at `87c7924` passed
[hosted verification](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/37136162108):
93 Linux / 94 macOS core tests, six native drawing/PDF tests, two offline
relaunch tests, four release-validation tests, Watch compilation, and inspected
unsigned device archive. Local validation passed 106 backend Paper/sync/media/seeder
tests and 27 Paper editor tests. The broad TypeScript check remains blocked by
errors outside the changed Paper files.

| Milestone                                      | Status                                         | Completion evidence still needed                                |
| ---------------------------------------------- | ---------------------------------------------- | --------------------------------------------------------------- |
| M0 — Build and distribution                    | Unsigned builds and UI tests passed            | Signing, TestFlight installation                                |
| M1 — Offline journal capture                   | Simulator capture/relaunch verified            | Device recording and connection checks                          |
| M2 — Durable background transfers              | Staging, retries, processing implemented       | Device expiration/recovery; autonomous system transfer design   |
| M3 — Multi-device data synchronization         | Implemented in part                            | Capture outbox migration, broader mutations, native validation  |
| M4 — Downloadable library                      | Text and active media implemented in part      | Remaining file types, background scheduling, storage refinement |
| M5 — Native drawing and annotation             | Native Paper saves and Linux previews verified | PDF integration, actual iPad and multi-device validation        |
| M6 — Mobile navigation and capture integration | Partly implemented                             | Share extension and iPad navigation refinement                  |
| M7 — Watch recording companion                 | Compiles; core tested                          | Signing and paired-device validation                            |
| M8 — Optional on-device speech and AI          | Runtime text-model check implemented           | Device readiness, speech, quality and resource measurements     |
| M9 — Release and recovery readiness            | Guide and replica migrations implemented       | Signed upgrade/restore tests and stable distribution            |

### Evidence from the initial implementation

- [x] 145 relevant backend tests passed across journal capture, attachments,
      multi-clip recording, journal routes, and screenshot journal tests.
- [x] 13 Swift core tests passed in a Swift 6.2 Linux container.
- [x] Native Swift source passed syntax parsing; this does **not** establish
      successful Apple SDK type checking or linking.
- [x] Project/workflow YAML parsed; new Markdown/YAML passed Prettier formatting.
- [x] Hosted Mac workflow has run successfully.
- [x] Offline capture/relaunch XCUITest has run successfully.
- [ ] A signed app has been installed on the user's devices.

Record subsequent verification below with the commit/build, command, result,
and any remaining limits. Do not promote “source written” to “device verified.”

## Agreed requirements and constraints

| Area              | Decision                                                                                                                                       |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| iPhone            | iPhone 16, user-reported iOS 26.6.2                                                                                                            |
| iPad              | 2021 12.9-inch M1 iPad Pro, Pencil 2, user-reported iPadOS 26.6.2                                                                              |
| Watch             | Apple Watch Series 7, user-reported watchOS 26.6                                                                                               |
| Existing computer | Early-2015 Intel MacBook on Monterey 12.7.6; do not require buying a new Mac                                                                   |
| Development       | Linux workspace; hosted macOS builds and tests; real devices for hardware behavior                                                             |
| Developer account | User has an Apple developer membership; team/signing setup remains outstanding                                                                 |
| Server            | Retain Linux Flask/SQLite server and existing local AI services                                                                                |
| Connectivity      | Keep Tailscale; use HTTPS within the tailnet; public exposure is not required                                                                  |
| Cellular          | Text/audio sync enabled over cellular by default, with a per-device option                                                                     |
| Bulk downloads    | Wi-Fi only; also account for a Wi-Fi hotspot being an expensive connection                                                                     |
| Device storage    | Both iPhone and iPad have 256 GB, typically more than 50 GB free; use measured free space and budgets, not an assumed entitlement to all of it |
| Offline scope     | Aim for the full active library and personal data needed by the mobile app, with archive media excluded by default                             |
| Knowledge         | Optional on each device; Wikipedia is a candidate collection, not a mandatory download                                                         |
| Mobile exclusions | Practice and Notebook do not need native mobile tabs                                                                                           |
| Drawing           | Native-quality Pencil drawing is a central reason for the Apple app                                                                            |
| Chat              | Server-backed native tab (passed in simulator); voice messages queue offline. Local chat is optional and requires evaluation                  |
| Todo              | Native tab (passed in simulator offline): daily tasks and to-dos, changes queued in an outbox, and a due-today/overdue badge                 |
| Watch controls    | Transcribe and Record; both preserve the original audio and create journal entries                                                             |

“Transcribe” means record and retain audio, create a journal entry, and request
transcription into its text. “Record” means create the entry with its recording
without requesting speech transcription. Neither action should require a live
server connection. Server enrichment remains separate from saving a capture.

Saving a YouTube link offline means preserving the URL and the user's thoughts
for later processing. It does not promise that a video which was never
downloaded will play offline.

## Architecture and invariants

These are implementation constraints for the work ahead. The precise sync API,
database schema, and drawing interchange format remain design work under their
milestones.

- Keep the Apple client alongside the React/Linux app. Native storage,
  recording, and drawing are the priority. Reuse suitable existing web screens
  where helpful; a complete SwiftUI rewrite of every feature is not required.
- Native UI and any embedded web UI must use the same device records and
  outbox. Do not create independent stores that disagree about what was saved.
- A local save succeeds before upload starts. Recording is independent of
  authentication, server reachability, transcription, and AI availability.
- Keep stable ULIDs and operation identities across restarts and retries.
  Treat a lost acknowledgement as a normal retry case.
- Preserve capture timestamps and the existing 4am journal-day semantics.
  Synchronization progress must use server-issued revisions/cursors rather
  than device wall clocks.
- Build a device database and separate media store for library replication.
  Do not synchronize the live server SQLite file or copy server credentials,
  provider configuration, and daemon job state into the mobile app.
- Keep unsynced originals distinct from downloadable copies. Storage cleanup
  must never evict an unsynced capture. A device copy is not a substitute for
  server backups.
- Acknowledge durable receipt at each transfer boundary. Preserve original
  recordings through server transcription and any local AI processing.
- Preserve competing edits until resolved. A refresh must not overwrite local
  unsynced text, strokes, or picture placement.
- Keep Tailscale/HTTPS and application authentication. Store credentials in
  Keychain/CI secrets, not source files or the downloadable data replica.
- Reuse existing server storage, journal, transcription, and AI job helpers.
  Preserve `LUNASCHAL_NO_SCHEDULERS` in tests and seed every new schema table.
- Work on approved feature branches. Commit, push, publish, or alter production
  only when authorized; adding a workflow does not authorize running a release.

## Milestones

### M0 — Hosted builds and first install

- [x] Create the approved `feat/apple-offline-foundation` branch in the Codex worktree.
- [x] Add the iPhone/iPad XcodeGen project and portable Swift package.
- [x] Add unsigned hosted Mac compilation/UI-test and Linux Swift-test jobs.
- [x] Run the workflow and resolve all Apple SDK compilation or simulator failures.
- [x] Supply the Apple team ID: `4AG98Q33RQ`, configured as the project/release default.
- [ ] Register the final bundle identifiers in the Apple team.
- [x] Generate opaque iOS/Watch app icons from the existing vector logo.
- [ ] Supply remaining required distribution metadata and validate App Store acceptance.
- [ ] Configure signing certificates/profiles and App Store Connect access as secrets.
- [x] Add an explicitly triggered signed archive/TestFlight release workflow.
      It builds the tip of `main` (recording, not gating on, its Apple CI result), validates both distribution
      profiles, uses a temporary keychain, exports by default, and uploads only when
      explicitly selected. Signing credentials and a real signed run remain pending;
      this does not establish device installation or App Store acceptance.
- [ ] Install a build on the iPhone and iPad and record the build identifier.
- [ ] Establish reproducible toolchain versions and a signing-renewal procedure.

Release preparation at `ff793ac` passed the unsigned generic iOS device archive,
Watch simulator build, seven native tests, 76 Linux / 77 macOS core tests, and
four signing-configuration tests
([run](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/37079353350)).
Privacy manifests now declare the API reasons used by each target. CI additionally
checks the embedded Watch identity/version, compiled assets, microphone descriptions,
and bundled privacy manifests; archive inspection passed at `c9c3c53`
([run](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/37096205883)).
Signed export, TestFlight processing, privacy questionnaire, and device installation
remain unverified and require the account setup in `apple/SIGNING.md`.

**Done when:** Linux development can produce a tested, signed build on a hosted
Mac and install it on both devices without the old MacBook building the app.

### M1 — Offline capture foundation

- [x] Native Capture / Journal / Settings screens.
- [x] Save typed entries offline and retain an unfinished typed draft locally.
- [x] Separate Record and Transcribe actions with original audio retention.
- [x] Durable per-capture manifests and separate audio files in Application Support.
- [x] Stable entry/attachment IDs and matching server acknowledgement checks.
- [x] Existing password/display-code login with session token in Keychain.
- [x] Bind the local capture store to its authenticated HTTPS server.
- [x] Foreground automatic upload/retry with configurable cellular access.
- [x] Read back server titles/transcripts for the most recent 30 synced captures.
- [x] Local audio playback/export and explicit interrupted-recording review.
- [x] Add optional `capturedAt` support to existing text/recording routes.
- [x] Test restart, lost-response retry, rejected uploads, auth expiry, and retained audio.
- [x] Run native offline capture/relaunch UI tests on the hosted Mac.
- [ ] Validate recording, playback, permissions, calls, screen lock, and Bluetooth on devices.
- [ ] Validate cellular-off and Tailscale-disconnected behavior on devices.
- [ ] Verify server transcripts and capture-day placement end to end.
- [ ] Deploy the matching backend change through the normal authorized workflow.

**Current limits:** uploads run while the app is active; photo import remains
outstanding. Historical journal download and text editing now use M3's replica.
Audio kept after a process kill may have an unfinalized AAC container
and be unplayable. Interrupted-file retention is not a crash-proof recorder.

**Done when:** a signed build can capture offline, reopen safely, and eventually
produce exactly one correctly dated server entry per capture on both devices.

### M2 — Transfer and recording durability

- [x] Persist recording upload bodies and recover cleanup after a durable acknowledgement.
- [x] Persist foreground attempt identities, bounded retry timing, authentication
      pauses, rejection state, and cancellation recovery.
- [x] Register and request background processing windows, with expiration
      cancellation, exactly-once completion, and a user enable/disable control.
- [ ] Verify processing delivery and expiration on physical devices over Tailscale.
- [ ] Design background URLSession transfers with durable task-to-operation mapping.
- [ ] Validate destination protection for background redirects; the existing
      foreground redirect delegate is not invoked for background sessions.
- [ ] Recover outstanding transfers after suspension, process termination, and restart.
- [ ] Define bounded retry/backoff, auth-required, rejected, and user-paused states.
- [ ] Reconcile completed uploads whose acknowledgement or local state write was lost.
- [ ] Support cancellation and changes to cellular policy during a transfer.
- [ ] Test underlying network policy through Tailscale, including cellular/hotspot changes.
- [ ] Expose transfer status and retry controls without repeated offline alerts.
- [ ] Evaluate a recoverable audio format or finalized segments to limit audio loss
      when recording is killed; do not upload incomplete containers silently.
- [ ] Handle low storage, microphone interruptions, and long recordings explicitly.
- [ ] Define resumable upload behavior for large captures if whole-file retries
      prove too costly; preserve the existing idempotency contract.

**Done when:** completed captures survive interrupted transfers and resume
without duplicates or data loss, while respecting the user's network settings.
Document OS scheduling limits instead of promising immediate background delivery.

**Implemented foundation:** recording requests are staged in their own device
directory with capture identity, destination, boundary, size, and SHA-256 digest.
Preparation publishes its manifest after the body exists. Retry reuses verified
bytes; missing/corrupt bodies rebuild from original audio. Credentials stay out
of manifests. Cleanup follows durable `.synced` state and recovers at the next
sync if termination interrupted it. Six portable regression tests cover reopen,
lost responses, destination mismatch, corruption/missing files, preparation
failure, and cleanup ordering. The ephemeral URLSession runs in the foreground
or during granted background processing time. Staging consumes space separately
from media downloads.

The foreground uploader now also keeps a per-capture attempt ledger. It persists
an attempt ID before sending, rejects obsolete completions, and recovers
interrupted attempts before replay. Retry delays grow from 30 seconds to 30
minutes across relaunches. Login resumes authentication-paused work; explicit
Retry upload resumes rejected captures; Sync overrides waiting delays.
Cancellation retains captures without increasing retry backoff. Ten deterministic
tests cover these transitions without sleeping or contacting a server. This
ledger does not yet contain URLSession task identifiers; future autonomous
background URLSession recovery must reconcile live system tasks before resetting
any sending state. Global upload pause controls
and server Retry-After handling remain outstanding.

**Remaining implementation sequence:**

Apple's [background-transfer documentation](https://developer.apple.com/documentation/Foundation/downloading-files-in-the-background)
states that background sessions follow redirects automatically and do not call
the redirect delegate. The current foreground client's `NoRedirects` guard
therefore cannot simply be copied into a background session. We now use
`BGProcessingTask` execution windows with the existing ephemeral session to keep
its redirect guard. Registration happens during application launch. Expiration
cancels the worker and transport; the durable outbox remains available for retry.
Scheduling respects authentication, pending work, retry deadlines, and the user
toggle. It does not repeatedly postpone an already requested window or schedule
bulk library downloads. Seven portable tests cover scheduling, failure recovery,
duplicate leases, expiration races, and retained capture/attempt state. All 60
Linux core tests pass; the native SDK build and existing simulator tests also
pass. Physical-device background scheduling and expiration remain unverified.
iOS controls delivery timing, and this does not keep uploads alive after process
termination. Before enabling autonomous background URLSession uploads, select
and test a destination-protection strategy, including
an HTTPS redirect to a different host, so neither credentials nor capture bytes
are silently forwarded. This is an outstanding design/test requirement, not a
claim that the current foreground uploader follows redirects.

1. Extend recording staging to text and ordered YouTube stages. Persist the
   system-task mapping before starting background requests; never put session
   tokens in the task manifest. Rebuilding staging must first reconcile or cancel
   any active system task that still owns its file.
2. Persist the task mapping and reconcile it with system tasks at relaunch.
   A missing task or lost reply must retry the same operation identities.
3. Validate server acknowledgements using the existing entry/attachment checks,
   save the resulting capture state, then remove disposable request bytes.
   Capture originals remain separate and retained.
4. Keep YouTube entry creation and link attachment as ordered durable stages;
   the capture is complete only after both acknowledgements.
5. Apply bounded retry/backoff and explicit authentication, rejection, cancellation,
   and network-policy states. Replacing a task after a cellular setting change
   must not lose its persisted operation or permit parallel duplicate scheduling.
6. Test each interruption boundary with fake transport first, then hosted native
   lifecycle tests and device suspension/Tailscale checks.

### M3 — Device database and multi-device sync

- [ ] Inventory the records needed by each mobile feature and classify them as
      synced data, local preferences, derived indexes, or server-only state.
- [x] Specify and version the sync API and minimum compatible server/client versions.
- [ ] Introduce the indexed local database; migrate the current capture outbox
      transactionally without losing manifests, IDs, files, or pending uploads.
- [x] Build a consistent paginated bootstrap plus server-issued change cursor.
- [x] Capture changes from every writer, including browser edits, imports, AI jobs,
      and schedulers—not only native-client writes.
- [x] Include deletion tombstones and a retention/rebootstrap policy for devices
      that have been offline longer than the server's change-history window.
- [x] Apply record batches and advance cursors atomically; resume interrupted bootstraps.
- [x] Add revision-checked mutations, stable operation IDs, and durable acknowledgements.
- [ ] Define text, drawing, media, and reading-progress conflict behavior separately.
- [x] Handle edit-versus-delete without resurrecting deleted rows or discarding edits.
- [x] Keep original text, server transcripts, and polished text distinct.
      Capture details retain typed originals and server raw text, show the matching
      recording's transcript, and keep the current journal body separate. Historical
      entries expose original text/dictation and each attachment's transcript.
      Matching uses attachment identity, never list position. Two portable tests
      cover persistence, legacy snapshots, and unrelated attachments; local Swift
      validation passes 80 tests. Native compilation and existing simulator
      regressions passed at `c3ee3f8`; dedicated transcript UI interaction remains
      untested.
- [x] Add offline search indexes and predictable schema migrations.
      Schema version 3 adds original journal dictation to existing FTS entries
      while retaining pending edits and cursors. Journal now searches device
      captures and downloaded entries; changing the search resets pagination,
      returning from a detail view preserves it. Pending edits remain visible
      during search. The capture/relaunch UI test verifies matching and
      non-matching searches at `ef78e21`; original-dictation index migration is
      covered by portable database tests, not a seeded historical-entry UI test.
- [ ] Define safe server-address changes, server restore detection, and account/device reset.
- [x] Extend server schema/seeding/tests together for any new tables.

**Implemented scope:** 17 allowlisted collections, SQLite FTS5 search, journal
text/title/tag update and deletion operations, explicit conflict preservation,
and manual sync-log compaction/restore epoch rotation. See the
[protocol notes](../backend/mobile_sync/README.md). Capture manifests remain
separate from the replica; broad feature inventory, media/drawing mutations,
automatic maintenance, device reset, and capture-outbox migration remain open.

**Done when:** phone, iPad, and Linux edits converge after offline operation;
conflicts are visible/recoverable; retries and bootstrap restarts are safe;
server credentials and operational state never enter the device replica.

### M4 — Library and storage management

- [ ] Inventory the active library, media roots, and archive collections with sizes.
- [x] Define a per-device collection selection screen and storage budget.
      Library controls select future media downloads and optional Knowledge,
      with a media budget. SQLite text and original captures are outside that budget.
- [ ] Include complete selected books/fics, archived web articles, PDFs, newspapers,
      and journal media—not only items previously opened in the UI.
- [x] Download collection manifests and files with stable identities, sizes, and hashes.
- [x] Resume partial downloads and verify integrity before marking files available.
- [ ] Default archive video/audio bytes to excluded while retaining useful metadata,
      thumbnails, commentary, and already-available transcripts.
- [ ] Allow explicit pinning of supported archived items without silently enabling
      bulk archive replication.
- [ ] Add Wi-Fi-only bulk scheduling, pause/resume, progress, and download-size estimates.
- [x] Distinguish “downloaded,” “metadata only,” “pending download,” and “unavailable” in file readers.
- [ ] Support offline browsing, reading, media playback, and search for downloaded content.
- [ ] Add safe removal of device copies, pinned-content rules, and low-space handling.
- [x] Keep unsynced capture cleanup separate from downloaded-library cleanup.
- [ ] Add optional Knowledge/ZIM downloads per device, including a local reading/search
      strategy and licensing/attribution for any bundled reader dependencies.
- [ ] Evaluate Wikipedia package choices against actual free space; keep them optional.

**Done when:** the selected active library opens after a cold offline launch,
without depending on browser caches or access to the server/archive drive.
Deleting a downloaded device copy must not delete the server original.

**Implemented scope:** selectable PDF-book and active Journal/Study/Paper/newspaper-cover
media; a configurable media budget (20 GB default); resumable foreground range
downloads; verified content-addressed storage; whole-device media-copy cleanup;
local PDF/image/audio/video/article views; per-item device-copy removal with
shared-file protection and a media-storage usage display. Archive video is excluded and
Knowledge article text is opt-in. Inline chapter images, full newspaper PDFs,
ZIM, archive pins, automatic eviction, whole-library sizing, and background
scheduling remain outstanding. Linux tests inject the file verifier; real
CryptoKit verification and native compilation passed on the hosted Mac; reader
interaction and Tailscale/cellular policy still need device validation.

PDF books now reuse the range-download and hash-verification path and open in
the native PDF reader. The server derives a book's path from its ID and rejects
symlinks outside that exact book location. Client capability negotiation skips
collections an older server does not support and explains how to enable PDF-book
downloads. Local validation passes 53 backend media/sync tests and 62 portable
Swift tests. A native PDFKit test additionally exercises a generated PDF through
partial download, store reopening, hash verification, and reader loading;
that test passed in the hosted iPhone simulator. Physical-device reading and
network-policy validation remain outstanding.

Individual completed downloads can be removed from the reader after confirmation.
The operation validates every saved media reference before removing anything;
shared content bytes stay while another record references them. Corrupt or
misidentified manifests fail without deletion. Captures, server records, partial
downloads, and unrelated files remain intact. Removal is disabled during a bulk
download; a future bulk download may restore the copy. Six portable regressions
cover sharing across reopen, partial/capture retention, corrupt references,
identity mismatch, repeated removal, and missing completed bytes. The native
build and existing simulator regressions pass. The new confirmation control
has not had an automated UI interaction test or physical-device validation.
Pinning and automatic eviction remain separate work.

File readers now persist and display last-known availability independently from
completed downloads: metadata only, pending, partially downloaded, downloaded,
or unavailable at the last server check. A missing or changed server file cannot
hide an existing verified copy. Partial progress belongs to the current hash;
complete but unverified bytes are not labelled downloaded. Per-item removal
retains the last server observation; whole-media cleanup clears it. Five portable
regressions cover these transitions across reopen, changed versions, unavailable
sources, removal, and mismatched identities. Native compilation and existing
simulator regressions passed; the new status labels have not had an automated
UI interaction test or physical-device validation.
The budget regression suite also covers oversized corrupt partials: discarding
one resets its offset before checking the full space needed to restart, so
corruption cannot bypass the configured media budget.

Offline journal, book, and document lists now expand beyond their initial 200
records using Load more controls. Library search applies to books and documents;
counts use the same FTS filter as results, and revision/ID ordering is stable.
Expansion reads the local database and preserves the expanded window during
refreshes; changing the search resets library windows to 200. Two new portable
tests cover larger libraries across reopen, filtered counts, deterministic order,
deletion, literal query handling, and invalid limits. All 75 local Swift tests
pass; hosted CI also passed 75 Linux / 76 macOS core tests, seven native
regression tests, and Watch compilation at `0fe5f32`
([run](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/36852919670)).
The Load more controls have not yet been driven by dedicated UI tests.
This expands the visible local results,
not the scope of collections downloaded from the server.

Downloaded Knowledge articles now have their own Library section, shared search,
Load more control, and selectable plain-text reader with title and summary.
Their existing download toggle stays opt-in; disabling future downloads retains
previously saved articles. The reader renders stored text without fetching
embedded images or pages. Markdown formatting, ZIM browsing, and article editing
remain outside this reader. A replica regression covers cold reopen, article-body
search, preservation when another collection is refreshed, and scoped deletion.
Native compilation and existing simulator regressions passed. The Knowledge
reader itself has not yet been driven by a dedicated UI test or checked on a
physical device.

Paper documents and newspaper front pages now have searchable Library sections
with Load more controls. Paper documents open read-only saved previews in page
order, including documents longer than 200 pages; this does not convert or
overwrite server ink. Newspaper search indexes migrate existing downloaded
records without a server request. Two portable regressions cover ordering,
document isolation, tombstones, and search migration with preserved cursors.
Local Swift validation passes 78 tests. Combined with the transcript changes,
[hosted verification at `c3ee3f8`](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/37095717125)
passed 80 Linux / 81 macOS core tests, seven native tests, the Watch build, and
the unsigned device archive. Dedicated Paper/newspaper UI interactions remain
untested.

Device-local reading positions now persist in the replica database: text chapters
resume at a paragraph, books offer a Continue link, and PDF books/documents/journal
attachment PDFs reopen at their saved page. Positions belong to a content version
(chapter epoch/revision or PDF hash), so replaced content starts at its beginning.
Deleted chapters cannot become resume targets. These positions never create
server edits; cross-device progress and conflict handling remain outstanding.
Two portable regressions pass in the 82-test local suite. A new PDFKit test covers
page restoration and navigation callbacks. Hosted verification at `c9c3c53`
passed 82 Linux / 83 macOS core tests, six native drawing/PDF tests, two offline
relaunch tests, Watch compilation, and the inspected unsigned device archive
([run](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/37096205883)).

### M5 — PencilKit drawing and annotations

Prototype this early, alongside M0–M2, because drawing quality is a primary
motivation. Final integration depends on the M3 conflict/storage contract.

- [ ] Prototype PencilKit on the actual M1 iPad/Pencil 2: latency, palm rejection,
      finger scrolling, zoom, eraser, selection, and Pencil 2 double-tap behavior.
- [ ] Inventory Paper, Study, newspaper/PDF annotations, and their coordinate systems.
- [x] Decide the canonical editable format and compatibility strategy for existing ink.
- [ ] Preserve originals during conversion; document any lossy import/export.
- [x] Keep native editable drawings plus portable previews for Linux/web readers.
- [x] Decide whether cross-platform editing can be lossless; clearly label any
      read-only or conversion-required paths rather than silently flattening ink.
- [ ] Preserve A4/page coordinates, page ordering, pasted images, and image transforms.
- [x] Implement local drawing checkpoints and crash/reopen recovery.
- [x] Preserve the existing distinction between local saving and the explicit Paper
      Save action until deliberately changing that interaction.
- [x] Synchronize page revisions with recoverable conflict copies.
- [ ] Integrate PDF/newspaper annotation and the Study split reading/drawing layout.
- [ ] Validate old pages, long documents, thumbnails, and Journal filing behavior.

**Done when:** drawing feels reliable on the target iPad, works offline, and
survives reopening/sync without losing existing ink or silently changing pages
on Linux. Do not assume Pencil Pro-only hardware features are available.

**Local workspace:** the Draw tab creates A4 PencilKit pages with
atomic native-ink/PNG checkpoints, undo, zoom, and export. Current and previous
checkpoint generations are retained. Explicit Save to Paper now publishes native
pages; existing web strokes are never converted. Six original portable persistence tests cover reopening, failed writes,
retention, renaming, previous-version recovery, and manifest identity checks.
The complete Linux Swift suite now passes 35 tests. PencilKit SDK compilation
passed; actual Pencil 2 behavior still needs device validation.

Drawing backup restoration now imports exported `.drawing` files as independent
editable pages. Native decoding and preview generation precede publication;
source bytes and existing pages are preserved. PNG/PDF imports are not converted
to editable strokes. Imports have a 64 MB limit and require at least one editable
stroke; blank drawings are rejected because PencilKit can also decode invalid
bytes as empty ink without throwing. Two additional portable tests pass (37 total
on Linux, 38 on Mac). The first hosted import run caught this silent-empty decode;
the corrected run passed all four native tests covering stroke preservation,
invalid input, blank ink, and the size limit. Files-provider interaction and
physical Pencil behavior remain device checks.

**Native Paper synchronization (2026-10-03):** explicit Save freezes a copy of
both files outside rolling checkpoints. Stable operation IDs, destination binding,
hash-checked acknowledgements and native-ink revision comparisons protect retries,
lost replies, same-second edits, deletions and server restore epochs. Conflicts
retain originals and offer a separate-copy workflow. A new save creates one
Paper/page; later ink updates preserve the server title and filing flag. Native
originals are a separate allowlisted `paper_native_ink` collection, with paths
excluded from replication. Its new table is included in the demo seeder. Changing
the collection projection deliberately rotates the sync epoch on server startup.

Library downloads include an optional native-ink media collection. Opening a
native page verifies both hashes before copying it into durable editable storage;
clean acknowledged copies may refresh, while local drafts and queued saves stay
untouched. Linux displays a read-only PNG, and server guards reject web stroke or
picture writes against native pages. Existing web pages stay editable on Linux.
The server retains current/previous native files, and both files are durable
before committing the row and replay receipt. A failed transaction can leave an
unreferenced file, never replace an older referenced original.

Final verification at `87c7924`: 93 Linux / 94 macOS Swift core tests, six native
drawing/PDF tests, two offline relaunch tests, Watch compilation and inspected
device archive passed in [hosted CI](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/37136162108).
Locally, 27 Paper editor tests and 106 backend Paper/sync/media/seeder tests passed.
Commands: `npx vitest run src/components/Paper/PaperEditor.test.tsx` and
`.venv/bin/pytest backend/tests/test_mobile_drawings.py backend/tests/test_mobile_sync.py backend/tests/test_mobile_media.py backend/tests/test_paper.py backend/tests/test_seed_test_db.py`.
Repository-wide
`tsc --noEmit` reports errors outside the changed feature (including missing
drizzle-kit/Node/Verovio declarations and existing Chat/Fanfic test fixtures).
Physical Pencil, multi-device delivery and signed upgrade checks remain open.

### M6 — Mobile navigation and capture integration

- [x] Start with a compact native Capture / Journal / Settings tab layout.
- [x] Map existing features to phone, iPad, web-only, or omitted mobile experiences.
      See the [development feature matrix](../apple/SUPPORTED_FEATURES.md).
- [x] Keep Practice and Notebook out of mobile navigation; distinguish the Notebook
      tab from the Paper/drawing features the iPad still needs.
- [ ] Finalize phone tabs and iPad sidebar/split-view navigation as features arrive.
- [x] Add historical Journal browsing, editing, attachment readers, and conflict resolution.
      New photo/document attachment capture and share-extension imports remain open.
- [x] Save YouTube URLs and commentary offline; queue server metadata/import work.
- [ ] Show archive playback availability without preventing URL/commentary capture.
- [ ] Add a share extension for links, audio, photos, and supported documents.
- [ ] Use a shared app container/outbox with safe handoff from the share extension.
- [ ] Reuse web readers/screens where appropriate with local content access and one
      shared data source; avoid embedding a server-dependent page as “offline.”
- [ ] Preserve useful keyboard access, accessibility labels, Dynamic Type, and rotation.
- [x] Capture a food log entry from the Capture draft (Save food entry); the server's
      `POST /api/food` now keeps the device's `capturedAt`. Browsing meals stays on the web.
- [x] Read the calendar offline: `calendar_events` and `calendar_event_exceptions` are
      read-only replica collections, synced as their own scope only when `/capabilities`
      lists them; `CalendarExpansion` ports `backend/calendar_recurrence.py`. Journal's
      toolbar has Sync on the left and a Journal/Calendar switch on the right. The Calendar
      page is the web's phone day view (`CalendarTimeline` mirrors `calendarDayLayout.ts`);
      creates, edits ("This and future" / "All events") and deletes (occurrence / this and
      future / whole series) queue in `CalendarOutbox` and show at once through
      `CalendarOverlay`. Ids are client ULIDs, including a split's new series (`newId` on
      `PATCH /api/calendar/<id>/from/<date>`), so a replay never duplicates. The six
      categories are checkboxes on the event's page, saved as soon as they're ticked
      (`PATCH` with `categoryTags` alone), and on the New event form; Edit leaves them out
      and holds Delete instead. A split now carries the categories into the new series. Overlapping events share their hours in lanes, with labels
      placed clear of every line in the group. Dragging an event queues a `reschedule`
      (one occurrence of a series becomes a move exception, as the web's drag does); a
      toggle at the bottom left switches the drag between moving and changing the length.
      Wake/sleep bands come from `GET /api/calendar/sleep/<date>` (derived on the server,
      so fetched and cached per day rather than replicated); hand-set times queue as a
      `PUT`. The server's rule is the desktop's: the first activity after 4am is the
      wake time and the last before the next 4am is bedtime. `backend/sleep.py` counts
      journal entries, chat messages, transcriptions, food, calorie logs, library reading
      spans, to-do ticks and removals, Paper writing, Study and opening a newspaper.
      What the phone queues offline (calorie logs, voice messages, to-do changes) now
      carries `capturedAt`, so a background sync at 03:00 isn't read as being awake;
      drawings published from the iPad don't yet. The per-event mic and zoom are still web-only. Passed in
      simulator; not verified on device.
- [x] Read the Journal as the desktop's feed: one timeline, newest first, with this
      device's unsynced captures interleaved by time. Each server entry's card shows its
      photos (a strip, full screen on a tap), voice clips played in place with their
      transcripts, videos and watched YouTube videos (poster, the archived copy played
      full screen, the source link and the summary). A categorised calendar event wraps
      the entries written during it in rings of its category colours
      (`JournalEventGroups` ports `src/lib/journalEventGroups.ts`; an item sits in one
      border, never two). Media comes from the library download's copy when there is one,
      otherwise it is fetched from `/api/journal/attachments/<id>/file` (or `/thumbnail`)
      and kept in Caches; a video with no device copy streams. A desktop clip is
      WebM/Opus, which AVFoundation can't open, so the phone asks for
      `?playable=1` and the server answers with an AAC copy made once by ffmpeg and kept
      beside the original (`backend/journal/playable.py`). Entries are read in
      `createdAt` order (`ReplicaStore.newestRecords`), no longer by sync revision.
      Passed in simulator, with a debug-only `-journalFeedFixture` launch argument
      seeding the UI test; not verified on device.
- [ ] Review Calendar, Lifestyle, Food, Learning, and other existing views before
      claiming mobile feature parity; full desktop parity is not a requirement.

**Done when:** routine phone capture and iPad reading/drawing are easy to reach,
and every exposed feature communicates its offline capabilities accurately.

### M7 — Watch recording companion

- [ ] Add the watchOS target and companion pairing/signing configuration.
- [x] Implement Record / Transcribe / Stop with clear recording and saved states.
- [x] Persist audio and capture metadata on the watch before attempting transfer.
- [x] Preserve IDs, capture time, and transcription intent through watch → phone → server.
- [x] Queue WatchConnectivity file transfers when the phone becomes available.
- [x] Acknowledge durable phone storage separately from server receipt; specify
      when a watch copy may be removed and what the status indicator means.
- [ ] Handle duplicate deliveries, interrupted transfers, watch/app restart, and
      a phone that has not yet configured or authenticated its server.
- [x] Respect the phone's cellular-upload preference after watch handoff.
- [ ] Handle microphone denial, interruptions, low storage, and long recordings.
- [ ] Test on the user's Series 7 with the phone absent and the server unavailable.
- [ ] Confirm both modes eventually create exactly one journal entry and retain audio.

**Done when:** a thought can be recorded away from the phone and server, then
arrive in the journal with its original time and intended transcription mode.
Direct watch-to-server connectivity is not required for this milestone.

**Implemented, not device-validated:** watchOS target, shared recorder, durable
phone inbox, hash/identity validation, replay-safe import, and separate phone
receipts. A durable server-upload receipt now enables a confirmed Remove Watch
copy action. The phone sends only after its capture is durably marked synced;
the Watch persists the receipt before acknowledging it. Both ends validate entry
and attachment identities. Retries survive relaunch and replay after removal;
the phone retains its original. Older imports can request status without sending
audio again. No automatic cleanup occurs, and Uploaded does not mean transcribed
or backed up. Four new portable tests cover receipt progression, identity mismatch,
active-recording protection, crash recovery during removal, and retained phone
audio; all 86 local Swift tests pass. Hosted verification at `228dec4` passed
86 Linux / 87 macOS core tests, eight native tests, Watch compilation, and the
inspected device archive
([run](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/37096771031)).
Actual paired-device receipt delivery and the Watch removal controls still need
hardware validation.
Microphone denial, initial low-space
checks, interruptions, and explicit playable-file recovery have code paths;
long-recording/storage-pressure and background behavior still need hardware
validation. Hosted CI now includes an unsigned Watch simulator build.

### M8 — Optional on-device speech and AI

Settings now checks the actual Foundation Models text-model availability and
current locale, distinguishes disabled/ineligible/not-ready states, and refreshes
when returning from device Settings. It follows Apple's
[runtime availability](https://developer.apple.com/documentation/foundationmodels/systemlanguagemodel)
and [locale checks](https://developer.apple.com/documentation/foundationmodels/supporting-languages-and-locales-with-foundation-models).
This does not download models, invoke generation, or transcribe audio. Native
SDK compilation and existing simulator regressions passed at `228dec4`;
real-device model readiness, speech support, and
quality evaluation remain open. Capture and server transcription do not depend
on this optional check.

- [ ] Check runtime availability by OS, hardware, locale, and downloaded model assets.
- [ ] Evaluate Apple's speech APIs for offline transcription on the iPhone and iPad.
- [ ] Compare representative recordings, names, languages, accuracy, latency,
      storage, battery use, and long-recording behavior against the server path.
- [ ] Define user choice between server transcription, local transcription, and fallback.
- [ ] Preserve original audio and transcript provenance; prevent duplicate text when
      local and server results arrive for the same recording.
- [ ] Evaluate Foundation Models for titles, summaries, and journal cleanup separately
      from speech recognition. AI availability must not gate capture or reading.
- [ ] Keep original text visible and protect user edits from delayed enrichment.
- [ ] Evaluate local chat with downloaded context and explicit capability limits.
- [ ] Keep server chat/tool behavior through the existing shared AI layer; do not
      assume the local model can perform unavailable server actions offline.

**Done when:** supported local features are useful, optional, and degrade cleanly.
Local chat is an optional extension, not a blocker for the core offline release.

### M9 — Release, upgrades, and recovery

- [ ] Test upgrades with pending uploads, interrupted recordings, downloaded files,
      old drawing formats, and database migrations.
- [ ] Test disk-full behavior at every local-save and transfer boundary.
- [ ] Test expired login, unavailable Tailscale, missing archive drive, and server downtime.
- [ ] Test server restore/reset and expired sync cursors without losing local edits.
- [ ] Define backup/export/restore behavior for device-only captures and drawings.
- [ ] Add actionable diagnostics without logging credentials or journal/audio content.
- [ ] Validate storage settings, accessibility, and large-library performance.
- [ ] Maintain repeatable TestFlight updates and a rollback/recovery procedure.
- [ ] Publish a supported-feature/device matrix and known limitations for each build.
      A [development feature matrix and recovery guide](../apple/SUPPORTED_FEATURES.md)
      now distinguishes implemented phone/iPad/Watch behavior, server dependencies,
      device-only originals, and unsupported paths. A device-validated signed
      release matrix remains pending.
- [ ] Decide the long-term personal distribution method separately from beta testing.

**Done when:** routine upgrades and common failure modes preserve the user's
work, and the documented supported experience matches device-tested behavior.

## Dependency order and next actions

1. **Now:** validate opportunistic background processing on devices and continue
   the remaining library and Paper integration;
   prepare signing using the user's Apple team and registered bundle identifiers
   following the [signing setup notes](../apple/SIGNING.md).
2. **First install:** complete M0 signing and validate M1 on iPhone/iPad.
3. **Early risk checks:** prototype PencilKit and recording recovery before
   committing to drawing formats or a full library schema.
4. **Foundation:** implement M2 and M3; library downloads depend on both.
5. **Daily use:** build M4, M5, and M6 incrementally with device-tested releases.
6. **Watch:** M7 can start once capture identities and durable phone handoff are
   stable; it does not need to wait for the full library.
7. **Optional AI:** M8 follows reliable capture and retrieval; M9 checks apply
   throughout, not only at the final release.

The proposed order is adjustable. Preserve the agreed behavior and record any
change in scope or architecture here before downstream implementation relies on it.

## Decisions still to make

| Decision                                                          | Needed for | Current position                                                                           |
| ----------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------ |
| Final bundle ID registration and signing credentials              | M0         | Team `4AG98Q33RQ` supplied; project uses `com.lunaschal.mobile` and its Watch companion ID |
| First library collection priorities and size budget per device    | M4         | Full active library is the goal; actual sizes not measured                                 |
| Broader mobile feature list                                       | M6         | Practice/Notebook omitted; other views need an inventory                                   |
| Drawing interchange and cross-platform editability                | M5         | PencilKit prototype must inform this                                                       |
| Sync conflict UX and deletion retention                           | M3         | Journal resolution implemented; other record types pending                                 |
| Local transcription languages and preferred server/local behavior | M8         | Server transcription first                                                                 |
| Long-term distribution                                            | M9         | Hosted builds/TestFlight are the initial route                                             |

These are staged decisions, not reasons to pause unrelated implementation.

## Tracking conventions

- Check a task only when that exact deliverable is complete. Keep implementation,
  automated verification, device verification, and deployment separate.
- Update the milestone table, relevant checkboxes, and verification log together.
- Add commit/PR/build references when they exist; do not invent them for local work.
- Keep technical setup instructions in `apple/README.md`; keep agreed scope and
  progress here. Link this tracker from the general roadmap.
- For a regression, reopen the affected checkbox and record the observed failure.

### Verification log

| Date       | Scope                                                   | Evidence                                                                                                                                                                         | Limits                                                                                            |
| ---------- | ------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| 2026-09-27 | Capture foundation, `e018be9`                           | 145 backend tests; 13 Swift core tests; Swift syntax parsing; YAML/format checks                                                                                                 | No hosted Mac run, native UI test execution, signing, or device validation                        |
| 2026-09-28 | Replica, conflicts, historical journal and library text | 173 backend regression tests; 21 Swift core tests; native Swift syntax parsing                                                                                                   | Apple SDK type checking and simulator/device execution still pending                              |
| 2026-09-28 | Sync-log compaction and restore epochs                  | 41 sync/seeder tests passed                                                                                                                                                      | Maintenance commands tested on isolated databases only                                            |
| 2026-09-29 | Native drawing recovery and Apple builds, `e71830d`     | [Hosted CI](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/36503732880): 35 Linux / 36 Mac core tests, Watch build, two iPhone relaunch tests                    | Unsigned simulator validation; Pencil and paired Watch hardware unverified                        |
| 2026-09-29 | Drawing import, `1e8b624`                               | [Hosted CI](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/36558448616): 37 Linux / 38 Mac core tests, four native import tests, two relaunch tests, Watch build | Source bytes preserved; blank imports rejected; Files-provider and Pencil hardware checks pending |

### Implementation entry points

Knowledge reader verification (2026-10-02, `e4b5ffd`): local and hosted Linux Swift
tests passed 76 cases; hosted macOS passed 77. The new regression proves that
article content remains searchable and readable after reopening and refreshing
another collection, and that deletion is scoped to Knowledge. Hosted native
checks passed five drawing/PDF tests, two offline relaunch tests, and the Watch
build ([CI](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/37075943078)).
No backend changes, signing, or production deployment were included.

Media availability verification (2026-10-01, `9d72cc5` and `80ea01c`):
local Swift tests passed 73 cases; hosted Linux passed 73 and macOS 74.
[Final native CI](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/36800042127)
passed five drawing/PDF tests, two offline relaunch tests, and Watch compilation.
Five new availability tests cover durable status and preservation of downloaded
copies; the existing budget regression now also checks oversized corrupt partials.
No backend changes, database migration, signing, or deployment were required.

Individual media removal verification (2026-09-30 Toronto / 2026-10-01 UTC,
`d4d8ee2`): local Swift tests passed 68 cases; hosted Linux passed 68 and macOS
69, including six new removal regressions. Hosted `xcodebuild test` passed five
drawing/PDF tests and two offline relaunch tests; the Watch build passed.
[CI result](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/36797973089).
No backend changes, signing, or production deployment were included.

PDF-book verification (2026-09-30, `de29953`): 53 backend tests passed with
`.venv/bin/pytest backend/tests/test_mobile_media.py backend/tests/test_mobile_sync.py`.
Local and hosted Linux `swift test` passed 62 tests; hosted macOS passed 63.
[Hosted native verification](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/36786909030)
passed five drawing/PDF tests and two offline relaunch tests, plus the Watch
build. The PDF test generates a real document, resumes a partial download,
verifies it with CryptoKit, removes the source, reopens the download store, and
checks page count and text through the app's PDFKit reader. No signing,
production deployment, or physical-device validation was performed.

Background processing verification (2026-09-30, `4d9abc3`):
[hosted CI](https://github.com/volodymyrkhrystynych/Lunaschal/actions/runs/36707854242)
passed 60 Linux / 61 macOS core tests, four native drawing-import tests, two
iPhone offline relaunch tests, and the Watch build. Seven new portable tests
exercise injected execution leases and scheduling, including cancellation of
an actual capture-sync operation with its durable attempt store. Simulator
tests verify launch registration does not break offline startup; they do not
simulate OS-granted processing time or establish real-device delivery timing.

Recording staging verification (2026-09-29, `96765d8`): local Swift 6.2 container
and hosted Linux `swift test` passed 43 tests; hosted Mac passed 44 including the
CryptoKit fixture. Hosted `xcodebuild test` passed four drawing-import tests and
two offline relaunch tests, and the Watch target compiled. Six new portable
staging tests cover retry/restart durability and cleanup ordering. They use an
injected verifier on Linux; production reuses the tested CryptoKit file hasher.
No background URLSession task or suspended-device transfer was exercised.

Media verification (2026-09-28, `a2be70d`): 47 backend media/sync tests and 25
portable Swift tests passed. The subsequent Watch handoff changes pass all 27
Swift tests, including temporary-file removal, duplicate receipt, corruption,
and isolation of a failed inbox item. Native phone/Watch views passed syntax
parsing only; Apple SDK type checking and the CryptoKit fixture await the hosted
build. No paired-device transfer has run.

Offline YouTube verification (2026-09-28): 100 backend tests across offline
capture, YouTube imports, sync, media, and seeding; 29 portable Swift tests.
Links preserve commentary, original capture time, and attachment identity on
retry. Old capture manifests remain readable. The URL is retained independently
of archive playback availability; share-extension capture is still outstanding.

- [Native app and build notes](../apple/README.md)
- [Signing and first installation](../apple/SIGNING.md)
- [Native screens and app state](../apple/App/)
- [Portable capture/sync package](../apple/LunaschalCore/)
- [Offline relaunch UI test](../apple/UITests/OfflineCaptureTests.swift)
- [Xcode project specification](../apple/project.yml)
- [Hosted build workflow](../.github/workflows/apple.yml)
- [Journal API](../backend/routes/journal.py) and [journal feature instructions](../backend/journal/CLAUDE.md)
- [Offline timestamp API tests](../backend/tests/test_journal_offline_capture.py)
- [Existing browser offline storage/queues](../src/offline/)
- [Paper feature instructions](../backend/paper/CLAUDE.md)
- [Knowledge architecture](knowledge-tab.md)
