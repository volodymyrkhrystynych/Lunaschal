"""Validate decoded distribution profiles and emit manual signing configuration.

Run only on a trusted release runner; never print profile or certificate contents.
"""

import argparse
import datetime as dt
import hashlib
import pathlib
import plistlib
import re

BUNDLES = ("com.lunaschal.mobile", "com.lunaschal.mobile.watchkitapp",
           "com.lunaschal.mobile.watchkitapp.widgets", "com.lunaschal.mobile.share")
# File stems on the runner and the xcconfig variable each profile fills, in BUNDLES order.
PROFILES = (("ios", "IOS"), ("watch", "WATCH"), ("complications", "COMPLICATIONS"), ("share", "SHARE"))
# The App Group each profile must carry. The Watch app hands its timer to the
# complications through the Watch group, and the share extension imports with
# the session the app leaves in the iPhone group; a profile without its group
# archives fine and then signs something that silently cannot work.
APP_GROUPS = {"com.lunaschal.mobile": "group.com.lunaschal.mobile",
              "com.lunaschal.mobile.share": "group.com.lunaschal.mobile",
              "com.lunaschal.mobile.watchkitapp": "group.com.lunaschal.mobile.watch",
              "com.lunaschal.mobile.watchkitapp.widgets": "group.com.lunaschal.mobile.watch"}


def validate_profile(profile, team, bundle, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if not re.fullmatch(r"[A-Z0-9]{10}", team):
        raise ValueError("Invalid Team ID")
    entitlements = profile.get("Entitlements", {})
    prefixes = profile.get("ApplicationIdentifierPrefix", [])
    if (profile.get("TeamIdentifier") != [team]
            or entitlements.get("com.apple.developer.team-identifier") != team
            or entitlements.get("application-identifier") not in
            [f"{prefix}.{bundle}" for prefix in prefixes]):
        raise ValueError("Profile team or app identifier does not match")
    group = APP_GROUPS.get(bundle)
    if group and group not in entitlements.get("com.apple.security.application-groups", []):
        raise ValueError(f"Profile for {bundle} lacks the {group} App Group")
    expiry = profile.get("ExpirationDate")
    if not isinstance(expiry, dt.datetime) or expiry.replace(tzinfo=dt.timezone.utc) <= now:
        raise ValueError("Profile is expired or missing expiration")
    if (entitlements.get("get-task-allow") is not False
            or "ProvisionedDevices" in profile or profile.get("ProvisionsAllDevices")):
        raise ValueError("An App Store distribution profile is required")
    uuid = profile.get("UUID", "")
    if not re.fullmatch(r"[A-Fa-f0-9]{8}(?:-[A-Fa-f0-9]{4}){3}-[A-Fa-f0-9]{12}", uuid):
        raise ValueError("Invalid profile UUID")
    certs = profile.get("DeveloperCertificates", [])
    if not certs or any(not isinstance(cert, bytes) or not cert for cert in certs):
        raise ValueError("Profile has no signing certificates")
    return uuid, {hashlib.sha1(cert).hexdigest().upper() for cert in certs}


def configuration(profiles, team, identities, build):
    if not re.fullmatch(r"[1-9][0-9]{0,3}(?:\.[0-9]{1,2}){0,2}", build):
        raise ValueError("Build number must use up to three numeric parts (4/2/2 digits)")
    validated = [validate_profile(p, team, b) for p, b in zip(profiles, BUNDLES, strict=True)]
    installed = set(re.findall(r'\b[0-9A-F]{40}\b', identities))
    common = installed.intersection(*(certs for _, certs in validated))
    if len(common) != 1:
        raise ValueError("Every profile must select one installed signing identity")
    certificate = common.pop()
    config = f"APPLE_TEAM_ID = {team}\nAPPLE_SIGNING_IDENTITY = {certificate}\n"
    config += f"CURRENT_PROJECT_VERSION = {build}\n"
    for (_, target), (uuid, _) in zip(PROFILES, validated, strict=True):
        config += f"LUNASCHAL_{target}_PROFILE_UUID = {uuid}\n"
    options = dict(method="app-store-connect", destination="export", signingStyle="manual",
                   teamID=team, signingCertificate=certificate,
                   manageAppVersionAndBuildNumber=False,
                   provisioningProfiles={bundle: value[0] for bundle, value in zip(BUNDLES, validated)})
    return config, options


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--team", required=True)
    parser.add_argument("--build", required=True)
    parser.add_argument("--directory", type=pathlib.Path, required=True)
    args = parser.parse_args()
    directory = args.directory
    profiles = [plistlib.loads((directory / f"{name}.plist").read_bytes()) for name, _ in PROFILES]
    config, options = configuration(profiles, args.team, (directory / "identities.txt").read_text(), args.build)
    (directory / "Signing.xcconfig").write_text(config)
    (directory / "ExportOptions.plist").write_bytes(plistlib.dumps(options))
    options["destination"] = "upload"
    (directory / "UploadOptions.plist").write_bytes(plistlib.dumps(options))


if __name__ == "__main__":
    main()
