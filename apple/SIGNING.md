# Signing and first installation

The unsigned hosted build works with Xcode 26.6. It compiles the iPhone/iPad
app and Watch companion and tests the iPhone app in Simulator. Simulator success
does not produce an installable device app. The existing 2015 MacBook is not
part of this build path.

## Account configuration

The user supplied Apple Developer Team ID **`4AG98Q33RQ`**. It is now the
default in the Xcode project and hosted release workflow. Actual membership and
profile ownership are verified when real distribution profiles are supplied.
The current bundle identifiers still need registration in that team:

| Target          | Bundle identifier                  |
| --------------- | ---------------------------------- |
| iPhone/iPad     | `com.lunaschal.mobile`             |
| Watch companion | `com.lunaschal.mobile.watchkitapp` |

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

The [release workflow](../.github/workflows/apple-release.yml) accepts a full
commit SHA and unique build number. That exact commit must have passed the
Apple app workflow on a trusted push or manual run. Upload defaults to false:
an archive/export run produces a signed IPA artifact retained for seven days.
Choosing `upload_to_testflight` separately uploads it for App Store Connect
processing; it does not select testers or publish an App Store release.

The workflow must be present on the default branch before GitHub exposes its
manual dispatch. Merging it and running a release remain separate authorized
actions. No signed run has been performed yet.

Configure a GitHub environment named `apple-release`, restrict its deployment
branches to trusted release branches, and require your review before it receives
credentials. The workflow defaults to `APPLE_TEAM_ID=4AG98Q33RQ`; an environment
variable named `APPLE_TEAM_ID` can override it. Configure these environment
secrets (base64 values must be a single unwrapped line):

- `APPLE_DISTRIBUTION_P12_BASE64` and `APPLE_DISTRIBUTION_P12_PASSWORD`.
- `APPLE_IOS_PROFILE_BASE64` and `APPLE_WATCH_PROFILE_BASE64`: App Store
  distribution profiles using the same certificate.
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
