# Signing and first installation

The unsigned hosted build works with Xcode 26.6. It compiles the iPhone/iPad
app and Watch companion and tests the iPhone app in Simulator. Simulator success
does not produce an installable device app. The existing 2015 MacBook is not
part of this build path.

## Account configuration

Both app targets declare `ITSAppUsesNonExemptEncryption: false` in
`project.yml`. The client uses Apple's HTTPS/Keychain services and SHA-256
integrity checks, with no bundled encryption implementation. This declares
exempt encryption and avoids the repeated TestFlight export-compliance
questionnaire for new builds containing the setting. It does not alter builds
already uploaded. Archive validation checks the declaration in both bundles.
Reassess it if encryption implementations or dependencies change; see
[Apple's declaration documentation](https://developer.apple.com/documentation/bundleresources/information-property-list/itsappusesnonexemptencryption).

The user supplied Apple Developer Team ID **`4AG98Q33RQ`**. It is now the
default in the Xcode project and hosted release workflow. Actual membership and
profile ownership are verified when real distribution profiles are supplied.
These bundle identifiers are registered in that team (confirmed by the user
on 2026-10-07). Use them as they are: a target whose identifier differs from
its registered App ID cannot get a distribution profile.

| Registered as            | Bundle identifier                          | Target in `project.yml` |
| ------------------------ | ------------------------------------------ | ----------------------- |
| Lunaschal                | `com.lunaschal.mobile`                     | `Lunaschal`             |
| Lunaschal Watch          | `com.lunaschal.mobile.watchkitapp`         | `LunaschalWatch`        |
| Watch face complications | `com.lunaschal.mobile.watchkitapp.widgets` | `LunaschalWatchWidgets` |
| iPhone widgets           | `com.lunaschal.mobile.widgets`             | Not built yet           |
| Share sheet              | `com.lunaschal.mobile.share`               | Not built yet           |

The last two are reserved for an iPhone widget extension and a share
extension. When one is built it needs its own App Store profile, a matching
entry in `apple/tools/release.py`'s `BUNDLES` and `PROFILES`, a check in
`apple/tools/check_archive.py`, and a secret in the release workflow, as the
complications have.

Two App Groups are registered in the team (confirmed by the user on
2026-10-07):

| Registered as   | App Group                          | Used by                                             |
| --------------- | ---------------------------------- | --------------------------------------------------- |
| Lunaschal Watch | `group.com.lunaschal.mobile.watch` | `LunaschalWatch` and `LunaschalWatchWidgets`        |
| Lunaschal       | `group.com.lunaschal.mobile`       | Nothing yet; for the iPhone widgets and share sheet |

The Watch app and its complications extension share
`group.com.lunaschal.mobile.watch`: the pomodoro timer and the recording status
live there, so the complications and Controls can read and start them. Enable
App Groups on both App IDs with that group selected, and only then create their
profiles, because a profile records the capabilities its App ID had when it was
made. A Watch profile made before the complications lacks the group and must be
regenerated. `apple/tools/release.py` refuses a Watch or complications profile
without it.

`group.com.lunaschal.mobile` is the iPhone side's. Leave it off the iPhone
target until something reads it: adding an entitlement means regenerating the
iPhone profile, and a profile without it fails the archive. When the iPhone
widgets or the share sheet are built, give that extension and the app this
group, and teach `release.py` to require it, as it does for the Watch.

Distribution profiles the user has created (as reported on 2026-10-07; the
profile files themselves live only in the release environment's secrets):

| Profile                   | For                                | Type      | Expires    | Notes                                                   |
| ------------------------- | ---------------------------------- | --------- | ---------- | ------------------------------------------------------- |
| Lunaschal iOS App Store   | `com.lunaschal.mobile`             | App Store | 2027-10-03 | App Groups and HealthKit among its enabled capabilities |
| Lunaschal Watch App Store | `com.lunaschal.mobile.watchkitapp` | App Store | 2027-10-03 | App Groups and HealthKit among its enabled capabilities |

Still needed for the complications: an App Store profile for
`com.lunaschal.mobile.watchkitapp.widgets` with
`group.com.lunaschal.mobile.watch`. The portal lists a profile's capabilities
but not which group each selects; `release.py` checks that the Watch and
complications profiles carry that group before anything is archived. A profile can
enable more capabilities than the app uses; it is the reverse that fails the
archive. `release.py` rejects an expired profile, so regenerate these before
2027-10-03.

The iPhone App ID needs the **HealthKit** capability enabled (Certificates,
Identifiers & Profiles → Identifiers → `com.lunaschal.mobile`), and any
provisioning profile must be regenerated after enabling it: the app is signed
with `com.apple.developer.healthkit` (from `project.yml`'s `entitlements`), and
a profile without it fails at install. The Watch target reads no Health data
itself and needs no capability.

The Watch target's `WKCompanionAppBundleIdentifier` must continue to match the
iPhone target. If the identifiers change, update both target settings and that
Info.plist property in `project.yml` together. If changing the background task
identifier, keep `BGTaskSchedulerPermittedIdentifiers` in `project.yml` and
`AppDelegate.syncIdentifier` identical. The same iPhone/iPad app serves
both devices; the Watch is its embedded companion.

Register the final identifiers in the user's team, then create the main app
record in App Store Connect before uploading a build. Apple describes the
[app-record setup](https://developer.apple.com/help/app-store-connect/create-an-app-record/add-a-new-app/)
and [Watch companion identifier](https://developer.apple.com/documentation/bundleresources/information-property-list/wkcompanionappbundleidentifier).

## Hosted signing setup

The [release workflow](../.github/workflows/apple-release.yml) accepts a unique
build number and builds the tip of `main` as it is when the run starts,
whether or not its Apple app workflow run has passed, finished or started.
The run summary names the commit and what its Apple app CI said (`success`,
`failure`, `in_progress`, `no run`), so an untested build is visible rather
than prevented. The selected commit stays fixed through approval and
archiving, even if `main` advances. (It used to fall back to the newest commit
that had passed, which shipped the previous commit instead of a fix whose
run had been cancelled at the time limit.) Upload defaults to false:
an archive/export run produces a signed IPA artifact retained for seven days.
Choosing `upload_to_testflight` separately uploads it for App Store Connect
processing; it does not select testers or publish an App Store release.

The workflow must be present on the default branch before GitHub exposes its
manual dispatch. Merging it and running a release remain separate authorized
actions.

Configure a GitHub environment named `apple-release`, restrict its deployment
branches to trusted release branches, and require your review before it receives
credentials. The workflow defaults to `APPLE_TEAM_ID=4AG98Q33RQ`; an environment
variable named `APPLE_TEAM_ID` can override it. Configure these environment
secrets (base64 values must be a single unwrapped line):

- `APPLE_DISTRIBUTION_P12_BASE64` and `APPLE_DISTRIBUTION_P12_PASSWORD`.
- `APPLE_IOS_PROFILE_BASE64`, `APPLE_WATCH_PROFILE_BASE64` and
  `APPLE_COMPLICATIONS_PROFILE_BASE64`: App Store distribution profiles using
  the same certificate.
- For upload only: `APPSTORECONNECT_API_KEY_P8_BASE64`,
  `APPSTORECONNECT_KEY_ID`, and `APPSTORECONNECT_ISSUER_ID`.

The runner validates profile expiry, exact identifiers, distribution type, and
the common installed signing identity before archiving. Each target selects its
own profile. Credentials live in a temporary keychain and temporary files;
an always-run cleanup removes them and installed profiles. Use GitHub-hosted
ephemeral runners for this workflow. Do not change it to a persistent runner
without reviewing keychain restoration and cleanup after cancellation.

`apple/tools/release.py` validates the provisional bundle IDs below; update its
`BUNDLES` constant and the expected IDs in `apple/tools/check_archive.py` alongside
`project.yml` if final IDs differ. The build number
uses Apple's numeric major/minor/patch shape (up to 4/2/2 digits); choose a new
number for each uploaded build. Simulator CI also builds an unsigned Release
archive, but that cannot verify your certificates, profile entitlements, export,
or App Store Connect acceptance.

Signing needs an Apple Distribution identity and App Store Connect provisioning
profiles matching the app and Watch identifiers, or an authenticated Xcode
provisioning flow that creates those assets. Distribution profiles select an
App ID and distribution certificate. See Apple's
[profile instructions](https://developer.apple.com/help/account/provisioning-profiles/create-an-app-store-provisioning-profile).

App Store Connect upload authentication is a separate credential from the
certificate that signs the app. An API key has a key ID, issuer information,
and a private `.p8` key; configure private material through GitHub Actions
secrets when the release workflow is ready. Do not put it in this repository
or the chat. Apple documents
[API keys](https://developer.apple.com/documentation/appstoreconnectapi/creating-api-keys-for-app-store-connect-api)
and [supported upload paths](https://developer.apple.com/help/app-store-connect/manage-builds/upload-builds).

No signing identities, profiles, API keys, or App Store records have been
created by the implementation so far. The Team ID can be shared as ordinary
configuration; it is not a private signing key.

Repository readiness check on 2026-10-03: no GitHub environments were configured,
and no repository secrets named `APPLE_*` or `APPSTORECONNECT_*` were present.
This checked setting names only, not secret values or the Apple account. The
Team ID configuration passed the four signing-helper tests and YAML consistency
checks; an actual signed archive still requires the credentials above.

## First device build checklist

Both targets include `PrivacyInfo.xcprivacy`. The current reasons cover app-only
preferences (`CA92.1`, phone/iPad), checking free space before recording/downloading
(`E174.1`), and metadata for files in the app container (`C617.1`). The mapping
follows Apple's [required API reason definitions](https://developer.apple.com/documentation/bundleresources/app-privacy-configuration/nsprivacyaccessedapitypes/nsprivacyaccessedapitype).
No device storage statistics are uploaded. These manifests do not complete the
App Store privacy questionnaire or encryption/export-compliance answers.
Review those against the final distribution and server configuration before
uploading; the app sends journal text and recordings to the user's configured
server and transfers Watch audio to the paired phone.

CI verifies that the device archive embeds the Watch app with matching versions,
compiled assets, microphone descriptions, and privacy manifests. This is a build
check, not an App Store validation response.

- Register the final app identifiers under team `4AG98Q33RQ`.
- Review the generated app icons and supply required app/distribution metadata.
- Verify the manually triggered signing/archive workflow with real credentials.
- Configure its signing and upload credentials.
- Export a signed archive from the same code that passed native CI.
- Explicitly upload the chosen build to TestFlight and wait for processing.
- Install on the iPhone and iPad; install the paired Watch companion.
- Record the build number and validate microphone/Pencil behavior, Tailscale,
  cellular policy, and Watch transfer without a debugger attached.

Track completion and exact build results in
[the implementation tracker](../docs/apple-offline-implementation.md). Native
capture and drawing must remain usable without signing into the Linux server.
