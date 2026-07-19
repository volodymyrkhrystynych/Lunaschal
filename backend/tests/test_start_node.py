"""start-node.sh guards its LUNASCHAL_URL input before launching anything.

The server is HTTPS-only (start-server.sh wires a Tailscale cert into Flask so
iOS Safari can access the mic), and the cert only validates for the server's
MagicDNS hostname. A stale http:// URL in .env previously let the script start
all three processes against a server that resets every connection — these tests
lock in the fail-fast instead.
"""
import os
import shutil
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def run_script(tmp_path):
    """Run a copy of start-node.sh from an empty dir so the repo root's .env
    (gitignored, machine-specific) can't leak a LUNASCHAL_URL into the test."""
    script = tmp_path / 'start-node.sh'
    shutil.copy(os.path.join(REPO_ROOT, 'start-node.sh'), script)

    def run(url=None):
        env = {'PATH': os.environ.get('PATH', '')}
        if url is not None:
            env['LUNASCHAL_URL'] = url
        return subprocess.run(
            ['bash', str(script)], env=env, capture_output=True, text=True, timeout=10,
        )

    return run


def test_missing_url_fails(run_script):
    result = run_script(url=None)
    assert result.returncode == 1
    assert 'LUNASCHAL_URL is not set' in result.stdout


def test_http_url_fails_with_https_hint(run_script):
    result = run_script(url='http://100.95.99.65:5000')
    assert result.returncode == 1
    assert 'https://' in result.stdout
    assert 'HTTPS-only' in result.stdout


def test_https_url_passes_the_guard(run_script):
    # A valid https URL gets past both guards; the script then fails on the
    # missing stt/run_listener.sh (we run a lone copy of the script), which
    # proves the exit came from later steps, not the URL validation.
    result = run_script(url='https://kozak.tail20a78b.ts.net:5000')
    assert 'LUNASCHAL_URL is not set' not in result.stdout
    assert 'HTTPS-only' not in result.stdout
