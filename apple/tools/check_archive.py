"""Check the built device archive, including the embedded Watch bundle."""
import pathlib
import plistlib
import sys


def check_archive(root):
    app = root / "Products/Applications/Lunaschal.app"
    watch = app / "Watch/LunaschalWatch.app"
    versions = []
    for bundle, identifier, platform in (
        (app, "com.lunaschal.mobile", "iPhoneOS"),
        (watch, "com.lunaschal.mobile.watchkitapp", "WatchOS"),
    ):
        info = plistlib.loads((bundle / "Info.plist").read_bytes())
        if info["CFBundleIdentifier"] != identifier or info["CFBundleSupportedPlatforms"] != [platform]:
            raise ValueError("Unexpected archive bundle identity or platform")
        versions.append((info["CFBundleShortVersionString"], info["CFBundleVersion"]))
        if info.get("ITSAppUsesNonExemptEncryption") is not False:
            raise ValueError("Archive is missing its exempt-encryption declaration")
        if not info.get("NSMicrophoneUsageDescription") or not (bundle / "Assets.car").is_file():
            raise ValueError("Archive is missing permission text or app assets")
        privacy = plistlib.loads((bundle / "PrivacyInfo.xcprivacy").read_bytes())
        reasons = {item["NSPrivacyAccessedAPIType"]: item["NSPrivacyAccessedAPITypeReasons"]
                   for item in privacy["NSPrivacyAccessedAPITypes"]}
        if privacy.get("NSPrivacyTracking") is not False or "E174.1" not in reasons.get("NSPrivacyAccessedAPICategoryDiskSpace", []):
            raise ValueError("Archive is missing its reviewed privacy declarations")
    if versions[0] != versions[1]:
        raise ValueError("Phone and Watch versions differ")
    watch_info = plistlib.loads((watch / "Info.plist").read_bytes())
    if watch_info.get("WKCompanionAppBundleIdentifier") != "com.lunaschal.mobile":
        raise ValueError("Watch companion points to another app")


if __name__ == "__main__":
    check_archive(pathlib.Path(sys.argv[1]))
    print("Device archive contains matching phone/Watch bundles, assets, and privacy manifests.")
