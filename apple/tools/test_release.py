import copy
import datetime as dt
import hashlib
import unittest

from release import APP_GROUPS, BUNDLES, configuration, validate_profile


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.team = "ABCDEFGHIJ"
        self.cert = b"fixture certificate, never a signing identity"
        self.identity = hashlib.sha1(self.cert).hexdigest().upper()
        self.profiles = [dict(
            UUID=f"00000000-0000-0000-0000-00000000000{i}",
            TeamIdentifier=[self.team], ApplicationIdentifierPrefix=["LEGACYPREF"],
            ExpirationDate=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1),
            DeveloperCertificates=[self.cert], Entitlements={
                "application-identifier": f"LEGACYPREF.{bundle}",
                "com.apple.developer.team-identifier": self.team,
                "get-task-allow": False,
                **({"com.apple.security.application-groups": [APP_GROUPS[bundle]]} if bundle in APP_GROUPS else {}),
            }) for i, bundle in enumerate(BUNDLES)]

    def test_generates_target_specific_profiles_and_matching_identity(self):
        config, options = configuration(self.profiles, self.team, self.identity, "12.2")
        self.assertIn("CURRENT_PROJECT_VERSION = 12.2\n", config)
        self.assertEqual(options["signingCertificate"], self.identity)
        self.assertEqual(options["destination"], "export")
        self.assertEqual(len(set(options["provisioningProfiles"].values())), len(BUNDLES))
        self.assertIn("LUNASCHAL_SHARE_PROFILE_UUID = 00000000-0000-0000-0000-000000000002\n", config)

    def test_app_and_share_profiles_need_the_shared_app_group(self):
        for index, bundle in enumerate(BUNDLES):
            if bundle not in APP_GROUPS:
                continue
            profile = copy.deepcopy(self.profiles[index])
            profile["Entitlements"]["com.apple.security.application-groups"] = ["group.other"]
            with self.subTest(bundle=bundle), self.assertRaises(ValueError):
                validate_profile(profile, self.team, bundle)

    def test_rejects_wrong_expired_and_non_distribution_profiles(self):
        mutations = [
            lambda p: p.update(TeamIdentifier=["OTHERTEAM1"]),
            lambda p: p.update(ExpirationDate=dt.datetime(2000, 1, 1)),
            lambda p: p.update(ProvisionedDevices=[]),
            lambda p: p.update(ProvisionsAllDevices=True),
            lambda p: p.update(UUID="bad\nsetting = injected"),
            lambda p: p.update(DeveloperCertificates=[]),
            lambda p: p["Entitlements"].update({"get-task-allow": True}),
            lambda p: p["Entitlements"].update({"application-identifier": "LEGACYPREF.*"}),
        ]
        for mutation in mutations:
            profile = copy.deepcopy(self.profiles[0])
            mutation(profile)
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                validate_profile(profile, self.team, BUNDLES[0])

    def test_requires_certificate_in_both_profiles_and_keychain(self):
        with self.assertRaises(ValueError):
            configuration(self.profiles, self.team, "", "1")
        self.profiles[1]["DeveloperCertificates"] = [b"different"]
        with self.assertRaises(ValueError):
            configuration(self.profiles, self.team, self.identity, "1")

    def test_rejects_invalid_build_and_team(self):
        for build in ("0", "10000", "1.100", "1.2.3.4", "1\nOTHER = yes"):
            with self.subTest(build=build), self.assertRaises(ValueError):
                configuration(self.profiles, self.team, self.identity, build)
        with self.assertRaises(ValueError):
            configuration(self.profiles, "bad\nTEAM", self.identity, "1")


if __name__ == "__main__":
    unittest.main()
