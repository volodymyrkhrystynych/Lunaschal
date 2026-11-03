# TODO

Manual steps that code can't do. Delete each one once it's done.

## iPhone widgets: signing setup (`feat/ios-habits-widget`)

The Habits and To-Do Home Screen widgets ship as a new extension, `LunaschalWidgets` (`com.lunaschal.mobile.widgets`). **Until these steps are done, the signed `apple-release` workflow fails on `main`**: `release.py` needs a profile for every bundle, including this one. Details are in [apple/SIGNING.md](apple/SIGNING.md).

- [x] Register the App ID `com.lunaschal.mobile.widgets` ("iPhone widgets"). It already exists.
- [ ] In the Apple Developer portal → Identifiers → **iPhone widgets**, enable **App Groups** and select `group.com.lunaschal.mobile`, the same group the app and share sheet use. Save.
- [ ] Profiles → **+** → **App Store Connect** → App ID `com.lunaschal.mobile.widgets` → the same distribution certificate the other profiles use → generate and download it.
- [ ] Store the profile as a secret in the `apple-release` environment:
  ```sh
  base64 -i ~/Downloads/<profile>.mobileprovision | tr -d '\n' \
    | gh secret set APPLE_WIDGETS_PROFILE_BASE64 --env apple-release
  ```
- [ ] Run `apple-release` and install the TestFlight build.
- [ ] Check on the device what the simulator couldn't test (an unsigned build has no App Group, so its widgets only say "Open Lunaschal…"):
  - Add both widgets to the Home Screen in small, medium and large sizes. Each shows real lists after the app has been opened once while signed in.
  - Ticking a habit and completing a to-do from the widget updates the widget at once, reaches the server, and appears in the app.
  - With no network, a tick made on the widget is kept, and the app sends it the next time it runs.
  - At 4am, habits untick, streaks carry over, and to-dos due tomorrow become due.
  - Tapping a widget anywhere except a check button opens the Todo tab.
- [ ] After the device checks pass, change "passed in simulator" to "verified on device" for the widgets in `apple/SUPPORTED_FEATURES.md` and `docs/apple-offline-implementation.md`.
