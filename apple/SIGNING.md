# Signing and first installation

The unsigned hosted build works with Xcode 26.6. It compiles the iPhone/iPad
app and Watch companion and tests the iPhone app in Simulator. Simulator success
does not produce an installable device app. The existing 2015 MacBook is not
part of this build path.

## Account information needed

The Apple Developer Team ID and final bundle identifiers are the next inputs.
Current project values are:

| Target          | Bundle identifier                  |
| --------------- | ---------------------------------- |
| iPhone/iPad     | `com.lunaschal.mobile`             |
| Watch companion | `com.lunaschal.mobile.watchkitapp` |

The Watch target's `WKCompanionAppBundleIdentifier` must continue to match the
iPhone target. If the identifiers change, update both target settings and that
Info.plist property in `project.yml` together. The same iPhone/iPad app serves
both devices; the Watch is its embedded companion.

Register the final identifiers in the user's team, then create the main app
record in App Store Connect before uploading a build. Apple describes the
[app-record setup](https://developer.apple.com/help/app-store-connect/create-an-app-record/add-a-new-app/)
and [Watch companion identifier](https://developer.apple.com/documentation/bundleresources/information-property-list/wkcompanionappbundleidentifier).

## Hosted signing setup

The release workflow is still outstanding. Its intended contract is a manual
dispatch for a specific tested commit, separate from the unsigned pull-request
checks. An archive/export run should be possible without publishing; TestFlight
upload should be an explicit release choice.

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

## First device build checklist

- Register final app identifiers and supply the Team ID.
- Supply app icons and required app/distribution metadata.
- Implement and verify the manually triggered signing/archive workflow.
- Configure its signing and upload credentials.
- Export a signed archive from the same code that passed native CI.
- Explicitly upload the chosen build to TestFlight and wait for processing.
- Install on the iPhone and iPad; install the paired Watch companion.
- Record the build number and validate microphone/Pencil behavior, Tailscale,
  cellular policy, and Watch transfer without a debugger attached.

Track completion and exact build results in
[the implementation tracker](../docs/apple-offline-implementation.md). Native
capture and drawing must remain usable without signing into the Linux server.
