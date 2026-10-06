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
TIP = 'c' * 40


def run(sha, **changes):
    return {**dict(head_sha=sha, event='push', head_branch='main', status='completed',
                   conclusion='success', head_repository={'full_name': REPO}), **changes}


class ReleaseSelectionTests(unittest.TestCase):
    def select(self, tip=TIP, runs=None, build='2', runs_error=False):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            summary = Path(directory) / 'summary'
            responses = [json.dumps({'sha': tip}).encode()]
            responses.append(OSError('GitHub unavailable') if runs_error
                             else json.dumps({'workflow_runs': runs or []}).encode())
            with patch.dict(os.environ, GH_REPO=REPO, BUILD_NUMBER=build,
                            GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(summary)), \
                    patch('subprocess.check_output', side_effect=responses):
                exec(compile(SCRIPT, str(WORKFLOW), 'exec'), {})
            self.assertIn(output.read_text().strip().split('=')[1], summary.read_text())
            return output.read_text(), summary.read_text()

    def test_ships_the_tip_of_main_even_when_its_ci_failed(self):
        output, summary = self.select(runs=[run(TIP, conclusion='failure')])
        self.assertEqual(output, f'commit={TIP}\n')
        self.assertIn('Apple app CI: failure', summary)

    def test_ships_the_tip_before_its_ci_has_run_or_finished(self):
        self.assertIn('Apple app CI: no run', self.select()[1])
        self.assertIn('Apple app CI: in_progress',
                      self.select(runs=[run(TIP, status='in_progress', conclusion=None)])[1])
        # A fork's run of the same commit says nothing about this repository's.
        self.assertIn('Apple app CI: no run',
                      self.select(runs=[run(TIP, head_repository={'full_name': 'fork/repo'})])[1])

    def test_a_failed_status_lookup_never_blocks_the_release(self):
        output, summary = self.select(runs_error=True)
        self.assertEqual(output, f'commit={TIP}\n')
        self.assertIn('Apple app CI: unknown', summary)

    def test_rejects_invalid_commit_and_build_number(self):
        with self.assertRaisesRegex(SystemExit, 'Invalid commit'):
            self.select(tip='not-a-sha')
        with self.assertRaisesRegex(SystemExit, 'Invalid build number'):
            self.select(build='invalid')


if __name__ == '__main__':
    unittest.main()
