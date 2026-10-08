"""Check the built device archive, including the embedded Watch bundle, its complications and the share extension."""
import pathlib
import plistlib
import re
import sys


def check_archive(root, build=None):
    """`build`, when given, is the number the release asked for. Each bundle
    must carry it, not XcodeGen's default of 1, or App Store Connect refuses
    the upload as a duplicate only after the whole archive has been built."""
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
        version = (info["CFBundleShortVersionString"], info["CFBundleVersion"])
        if not all(re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", part) for part in version):
            raise ValueError(f"Archive bundle version is not numeric: {version}")
        if build is not None and version[1] != build:
            raise ValueError(f"Archive carries build {version[1]}, not the requested {build}")
        versions.append(version)
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
    complications = watch / "PlugIns/LunaschalWatchWidgets.appex"
    info = plistlib.loads((complications / "Info.plist").read_bytes())
    if (info["CFBundleIdentifier"] != "com.lunaschal.mobile.watchkitapp.widgets"
            or info["CFBundleSupportedPlatforms"] != ["WatchOS"]
            or info.get("NSExtension", {}).get("NSExtensionPointIdentifier") != "com.apple.widgetkit-extension"):
        raise ValueError("Unexpected complication extension identity or platform")
    if (info["CFBundleShortVersionString"], info["CFBundleVersion"]) != versions[1]:
        raise ValueError("Watch and complication versions differ")
    if not (complications / "PrivacyInfo.xcprivacy").is_file():
        raise ValueError("Complication extension is missing its privacy manifest")
    share = app / "PlugIns/LunaschalShare.appex"
    info = plistlib.loads((share / "Info.plist").read_bytes())
    if (info["CFBundleIdentifier"] != "com.lunaschal.mobile.share"
            or info["CFBundleSupportedPlatforms"] != ["iPhoneOS"]
            or info.get("NSExtension", {}).get("NSExtensionPointIdentifier") != "com.apple.share-services"):
        raise ValueError("Unexpected share extension identity or platform")
    if (info["CFBundleShortVersionString"], info["CFBundleVersion"]) != versions[0]:
        raise ValueError("Phone and share extension versions differ")
    if info.get("ITSAppUsesNonExemptEncryption") is not False:
        raise ValueError("Share extension is missing its exempt-encryption declaration")
    if not (share / "PrivacyInfo.xcprivacy").is_file():
        raise ValueError("Share extension is missing its privacy manifest")
    watch_info = plistlib.loads((watch / "Info.plist").read_bytes())
    if watch_info.get("WKCompanionAppBundleIdentifier") != "com.lunaschal.mobile":
        raise ValueError("Watch companion points to another app")


if __name__ == "__main__":
    check_archive(pathlib.Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else None)
    print("Device archive contains matching phone/Watch/share bundles, assets, and privacy manifests.")
