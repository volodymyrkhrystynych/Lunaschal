import pathlib
import plistlib
import tempfile
import unittest

from check_archive import check_archive


class CheckArchiveTests(unittest.TestCase):
    def archive(self, version="0.1.0", build="2", share_id="com.lunaschal.mobile.share", share_build=None):
        root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        app = root / "Products/Applications/Lunaschal.app"
        for bundle, identifier, platform in (
            (app, "com.lunaschal.mobile", "iPhoneOS"),
            (app / "Watch/LunaschalWatch.app", "com.lunaschal.mobile.watchkitapp", "WatchOS"),
        ):
            bundle.mkdir(parents=True)
            info = dict(CFBundleIdentifier=identifier, CFBundleSupportedPlatforms=[platform],
                        CFBundleShortVersionString=version, CFBundleVersion=build,
                        ITSAppUsesNonExemptEncryption=False, NSMicrophoneUsageDescription="Record",
                        WKCompanionAppBundleIdentifier="com.lunaschal.mobile")
            (bundle / "Info.plist").write_bytes(plistlib.dumps(info))
            (bundle / "Assets.car").write_bytes(b"assets")
            privacy = dict(NSPrivacyTracking=False, NSPrivacyAccessedAPITypes=[dict(
                NSPrivacyAccessedAPIType="NSPrivacyAccessedAPICategoryDiskSpace",
                NSPrivacyAccessedAPITypeReasons=["E174.1"])])
            (bundle / "PrivacyInfo.xcprivacy").write_bytes(plistlib.dumps(privacy))
        share = app / "PlugIns/LunaschalShare.appex"
        share.mkdir(parents=True)
        (share / "Info.plist").write_bytes(plistlib.dumps(dict(
            CFBundleIdentifier=share_id, CFBundleSupportedPlatforms=["iPhoneOS"],
            CFBundleShortVersionString=version, CFBundleVersion=share_build or build,
            ITSAppUsesNonExemptEncryption=False,
            NSExtension=dict(NSExtensionPointIdentifier="com.apple.share-services"))))
        (share / "PrivacyInfo.xcprivacy").write_bytes(plistlib.dumps(dict(NSPrivacyTracking=False)))
        return root

    def test_accepts_the_requested_build(self):
        check_archive(self.archive(build="1.4"), "1.4")
        check_archive(self.archive(build="2"))

    def test_rejects_an_archive_that_ignored_the_build_number(self):
        # What every release since the first one uploaded: XcodeGen's 1 / 1.0.
        with self.assertRaisesRegex(ValueError, "not the requested 2"):
            check_archive(self.archive(version="1.0", build="1"), "2")

    def test_rejects_an_unexpanded_build_setting(self):
        with self.assertRaisesRegex(ValueError, "not numeric"):
            check_archive(self.archive(build="$(CURRENT_PROJECT_VERSION)"))

    def test_rejects_a_share_extension_that_is_not_ours_or_out_of_step(self):
        with self.assertRaisesRegex(ValueError, "share extension identity"):
            check_archive(self.archive(share_id="com.example.share"))
        with self.assertRaisesRegex(ValueError, "share extension versions differ"):
            check_archive(self.archive(build="3", share_build="1"))


if __name__ == "__main__":
    unittest.main()
