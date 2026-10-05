"""Exercise the workflow's selection script without GitHub or signing credentials."""
import json
import os
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import patch


WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/apple-release.yml'
SCRIPT = textwrap.dedent(WORKFLOW.read_text().split("python3 - <<'PY'\n", 1)[1]
                         .split('\n          PY', 1)[0])
REPO = 'owner/repo'
NEW, OLD, TIP = 'a' * 40, 'b' * 40, 'c' * 40


def run(sha, **changes):
    return dict(head_sha=sha, event='push', head_branch='main',
                conclusion='success', head_repository={'full_name': REPO}, **changes)


class ReleaseSelectionTests(unittest.TestCase):
    def select(self, runs, history, build='2'):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            summary = Path(directory) / 'summary'
            responses = [json.dumps([{'workflow_runs': runs}]).encode(),
                         json.dumps({'sha': TIP}).encode()]
            responses.extend(json.dumps(page).encode() for page in history)
            with patch.dict(os.environ, GH_REPO=REPO, BUILD_NUMBER=build,
                            GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(summary)), \
                    patch('subprocess.check_output', side_effect=responses):
                exec(compile(SCRIPT, str(WORKFLOW), 'exec'), {})
            self.assertIn(output.read_text().strip().split('=')[1], summary.read_text())
            return output.read_text()

    def test_prefers_newer_commit_over_recent_run_of_old_code(self):
        self.assertEqual(self.select([run(OLD), run(NEW)],
                                    [[{'sha': TIP}, {'sha': NEW}, {'sha': OLD}]]),
                         f'commit={NEW}\n')

    def test_skips_untrusted_runs_and_pages_through_history(self):
        invalid = []
        for changes in ({'event': 'pull_request'}, {'head_branch': 'feature'},
                        {'conclusion': 'failure'}, {'head_repository': {'full_name': 'fork/repo'}}):
            candidate = run(NEW)
            candidate.update(changes)
            invalid.append(candidate)
        self.assertEqual(self.select(invalid + [run(OLD)],
                                    [[{'sha': NEW}], [{'sha': OLD}]]), f'commit={OLD}\n')

    def test_rejects_missing_success_or_commit_removed_from_main(self):
        for runs, history in (([], []), ([run(OLD)], [[{'sha': TIP}], []])):
            with self.subTest(runs=runs), self.assertRaises(SystemExit):
                self.select(runs, history)

    def test_rejects_invalid_build_number(self):
        with self.assertRaisesRegex(SystemExit, 'Invalid build number'):
            self.select([], [], build='invalid')


if __name__ == '__main__':
    unittest.main()
