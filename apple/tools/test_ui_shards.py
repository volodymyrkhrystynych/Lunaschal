import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from ui_shards import APP_TEST_SECONDS, DEFAULT_SECONDS, DURATIONS, shard, tests

UITESTS = pathlib.Path(__file__).resolve().parents[1] / "UITests"


class UIShardTests(unittest.TestCase):
    def test_finds_test_methods_and_not_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            pathlib.Path(directory, "A.swift").write_text(
                "final class ACase: XCTestCase {\n"
                "    private func tab(_ app: XCUIApplication) {}\n"
                "    func testOne() {\n    }\n"
                "    func testTwo() async throws {}\n"
                "}\n")
            # `async throws` comes after the parentheses, so it is still found.
            self.assertEqual(tests(directory), ["ACase/testOne", "ACase/testTwo"])

    def test_every_real_ui_test_lands_in_exactly_one_shard(self):
        names = tests(UITESTS)
        self.assertGreater(len(names), 10)
        durations = json.loads(DURATIONS.read_text())
        shards = [shard(names, index, 5, durations) for index in range(1, 6)]
        self.assertEqual(sorted(sum(shards, [])), names)
        self.assertTrue(all(shards))
        loads = [sum(durations.get(name, DEFAULT_SECONDS) for name in group)
                 for group in shards]
        loads[0] += APP_TEST_SECONDS
        self.assertLess(max(loads) - min(loads), DEFAULT_SECONDS)

    def test_slow_tests_are_spread_even_when_their_names_would_cluster(self):
        names = [f"Case/test{i}" for i in range(6)]
        durations = dict(zip(names, [180, 10, 10, 180, 10, 10]))
        groups = [shard(names, i, 3, durations) for i in range(1, 4)]
        self.assertFalse(any(names[0] in group and names[3] in group for group in groups))
        self.assertEqual(len(groups[0]), 4)  # app tests plus the four short UI tests

    def test_new_tests_get_default_weight_and_removed_tests_are_ignored(self):
        names = ["Case/testKnown", "Case/testNew"]
        durations = {"Case/testKnown": DEFAULT_SECONDS, "Case/testRemoved": 9999}
        groups = [shard(names, i, 2, durations) for i in (1, 2)]
        self.assertEqual(sorted(sum(groups, [])), names)
        self.assertTrue(all(groups))

    def test_assignment_does_not_depend_on_discovery_order(self):
        names = tests(UITESTS)
        durations = json.loads(DURATIONS.read_text())
        for i in range(1, 6):
            self.assertEqual(shard(names, i, 5, durations),
                             shard(list(reversed(names)), i, 5, durations))

    def test_cli_selects_all_tests_once_and_fails_for_an_empty_shard(self):
        script = pathlib.Path(__file__).with_name("ui_shards.py")
        selected = []
        for i in range(1, 6):
            result = subprocess.run([sys.executable, str(script), str(UITESTS), str(i), "5"],
                                    capture_output=True, text=True, check=True)
            selected.extend(result.stdout.splitlines())
        self.assertEqual(sorted(selected),
                         [f"-only-testing:LunaschalUITests/{name}" for name in tests(UITESTS)])
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(script), directory, "1", "5"],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")

    def test_rejects_a_shard_outside_the_total(self):
        with self.assertRaises(ValueError):
            shard(["A/test"], 4, 3)


if __name__ == "__main__":
    unittest.main()
