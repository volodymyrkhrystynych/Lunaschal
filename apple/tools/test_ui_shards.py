import pathlib
import tempfile
import unittest

from ui_shards import shard, tests

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
        shards = [shard(names, index, 3) for index in (1, 2, 3)]
        self.assertEqual(sorted(sum(shards, [])), names)
        self.assertTrue(all(shards))
        self.assertLessEqual(max(map(len, shards)) - min(map(len, shards)), 1)

    def test_rejects_a_shard_outside_the_total(self):
        with self.assertRaises(ValueError):
            shard(["A/test"], 4, 3)


if __name__ == "__main__":
    unittest.main()
