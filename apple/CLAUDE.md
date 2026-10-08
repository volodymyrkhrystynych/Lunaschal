# apple/CLAUDE.md

Working on the native iPhone/iPad/Watch client. What the client does and what is still outstanding is in [README.md](README.md), [SUPPORTED_FEATURES.md](SUPPORTED_FEATURES.md) and [docs/apple-offline-implementation.md](../docs/apple-offline-implementation.md); signing and release in [SIGNING.md](SIGNING.md). This file is about how to work on it.

**A Mac is available only occasionally.** Most development happens on Linux, where only `LunaschalCore` can be built and tested. So a Mac session is the chance to do whatever Linux cannot: anything in `App/` or `Watch/`, simulator UI tests, and installing on the real devices. Spend Mac time on those, and push logic down into `LunaschalCore` so the next change to it can be tested without a Mac.

## Mac setup

Most of this isn't preinstalled, and every missing piece fails with a message that doesn't name the fix:

- **Xcode 26.6** (matches CI's `DEVELOPER_DIR`). Simulator runtimes: iOS 26.5 **and watchOS 26.5**.
- **The watchOS runtime is required even for iPhone-only work.** The `Lunaschal` scheme embeds the Watch app, so `xcodebuild test` refuses to start without it: `watchOS 26.5 must be installed in order to test the scheme`. Install with `xcodebuild -downloadPlatform watchOS` (~4 GB).
- **XcodeGen**: `brew install xcodegen`. The `.xcodeproj` is generated and gitignored. Never edit it by hand; edit `project.yml`.
- **Python ≥ 3.10 for `tools/`.** macOS's `/usr/bin/python3` is 3.9, and `release.py` uses `zip(strict=True)`, so three release tests fail with `TypeError: zip() takes no keyword arguments`. That's the interpreter, not a bug. `brew install python@3.13` and run `python3.13` (Homebrew installs it versioned only).

## Commands

Run from the repo root:

```sh
swift test --package-path apple/LunaschalCore                     # core logic, also runs on Linux
python3.13 -m unittest discover -s apple/tools -p 'test_*.py'      # release/signing helpers

swift apple/tools/make_icons.swift                                 # once, before generating
xcodegen generate --spec apple/project.yml                         # after editing project.yml OR adding/removing a file

# iPhone: unit tests (AppTests) + UI tests (UITests). UI tests are slow, ~60 s for three.
xcodebuild -project apple/Lunaschal.xcodeproj -scheme Lunaschal \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -parallel-testing-enabled NO -derivedDataPath apple/DerivedData CODE_SIGNING_ALLOWED=NO test

# iPad-only UI tests (CI's ipad job runs these, plus the notebook test)
#   add: -destination 'platform=iOS Simulator,name=iPad Pro 13-inch (M5)'
#        -only-testing:LunaschalUITests/OfflineCaptureTests/testDrawingWorkspaceReopensWithoutAServer
#        -only-testing:LunaschalUITests/OfflineCaptureTests/testLibraryCategoriesAndDownloadSettingsAreSeparate
#        -only-testing:LunaschalUITests/OfflineCaptureTests/testNewspaperScrollsAsOneColumn
#        -only-testing:LunaschalUITests/OfflineCaptureTests/testNotesPagesTurnWithASwipe

xcodebuild -project apple/Lunaschal.xcodeproj -scheme LunaschalWatch \
  -sdk watchsimulator -destination 'generic/platform=watchOS Simulator' \
  -derivedDataPath apple/WatchDerivedData CODE_SIGNING_ALLOWED=NO build

# unsigned device archive + the same inspection CI does
xcodebuild -project apple/Lunaschal.xcodeproj -scheme Lunaschal -configuration Release \
  -destination 'generic/platform=iOS' -archivePath apple/Lunaschal.xcarchive CODE_SIGNING_ALLOWED=NO archive
python3.13 apple/tools/check_archive.py apple/Lunaschal.xcarchive
```

**XcodeGen collects sources by folder**, so a new `.swift` file in `App/`, `Watch/`, `AppTests/` or `UITests/` isn't compiled until you regenerate. A file that seems to be ignored usually just hasn't been picked up yet. `LunaschalCore` is an SPM package and picks up new files on its own.

The CI equivalent is `.github/workflows/apple.yml`. Match it rather than inventing new flags. It runs on pull requests and by hand, never on a push to `main`, as parallel jobs: Linux core and tool tests, the device archive with the Watch build, three shards of the iPhone UI tests (`tools/ui_shards.py` splits them by name, and the first shard also runs the app unit tests), and the iPad tests. A failing simulator test gets one retry, and a test stuck for 5 minutes is stopped. The signed release (`apple-release.yml`) runs no tests; it builds whatever is at the tip of `main`.

## Layout and where code belongs

- **`LunaschalCore/`** holds persistence, sync, request formats, retry/backoff, the SQLite replica, media verification and Watch handoff/receipts. CI also tests it in a `swift:6.2` **Linux** container, so it must compile without Apple frameworks. Linux-only and Apple-only imports are guarded with `#if canImport(...)` (see `JournalAPI.swift` for `FoundationNetworking`, `MediaStore.swift` for `CryptoKit`). SQLite comes in through the `CSQLite` system-library shim, not a package. Anything testable without UIKit/SwiftUI belongs here, with an XCTest beside it.
- **`App/`** is the SwiftUI app for iPhone and iPad (one target, `TARGETED_DEVICE_FAMILY 1,2`). Its Swift language mode is **5.0** (`SWIFT_VERSION` in `project.yml`), not 6.
- **`Share/`** is the share extension (`LunaschalShare`): a shared fic link goes to the server's import route. **`Shared/` is compiled into both the app and the extension** — the sign-in copy they share through the `group.com.lunaschal.mobile` App Group. Without signing (every simulator build here) there are no entitlements, so that copy is never made and the extension can only say "sign in first"; test its logic in `LunaschalCore` (`FicImport.swift`) instead.
- **`Watch/`** is the watchOS app. **`App/Recorder.swift` is compiled into both targets**, so anything added to it must build for watchOS too: no UIKit, no iOS-only AVAudioSession options. The Watch scheme build catches this; the iPhone build doesn't.
- **`AppTests/`** holds unit tests that need the app module (PDFKit, PencilKit, drawing import). **`UITests/`** holds XCUITests. They run with no server: they test offline capture, drawing and library UI, and must never depend on reaching a backend.
- The server side is `backend/mobile_sync/` and `backend/tests/test_mobile_*.py`. A change to the sync protocol is a two-sided change, so run both pytest and `swift test`. The server's projections must never export credentials or stored filesystem paths.

## Things that must change together

- **Bundle identifiers** live in `project.yml` (both targets, plus the Watch's `WKCompanionAppBundleIdentifier`), `tools/release.py`'s `BUNDLES` and `tools/check_archive.py`.
- **The background task id** lives in `project.yml`'s `BGTaskSchedulerPermittedIdentifiers` and `AppDelegate.syncIdentifier`.
- **Scope changes** go into README.md, SUPPORTED_FEATURES.md and the implementation tracker. Those documents make claims about what is and isn't verified, so keep "passed in simulator" and "verified on device" separate when updating them.

## Devices and signing

**Local signing isn't part of the workflow. Simulator testing is the bar on a Mac.** Build and test with `CODE_SIGNING_ALLOWED=NO`, as the commands above do. Don't set up certificates, profiles or Xcode accounts to get a device build. Builds reach the real devices through the GitHub `apple-release` workflow and TestFlight (see SIGNING.md). On device, the user checks what the simulator can't: microphone and lock-screen recording, Pencil, WatchConnectivity transfers, Tailscale/cellular switching. So when updating the docs, call a change "passed in simulator", not "verified on device".

**Team `4AG98Q33RQ` in `project.yml` is the user's. Never change it, and never point a build at whatever identities a Mac's keychain happens to hold.** A borrowed Mac may carry someone else's team, and signing against it would register Lunaschal's bundle ids there.
