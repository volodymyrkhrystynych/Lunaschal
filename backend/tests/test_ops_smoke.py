"""backend/ops/smoke.py is what stands between a merge that cannot boot and a
production restart. It runs in its own process, as the watcher runs it, so the
ambient test database and the already-imported app cannot leak into it."""

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from backend.ops.smoke import copy_db

REPO = Path(__file__).resolve().parents[2]


def _smoke(db: Path, cwd: Path, checkout: Path = REPO) -> subprocess.CompletedProcess:
    """Exactly as the watcher runs it: this repo's file, by path, on a checkout."""
    env = {k: v for k, v in os.environ.items()
           if k not in ('DATABASE_URL', 'LUNASCHAL_NO_SCHEDULERS', 'PYTHONPATH')}
    return subprocess.run(
        [sys.executable, str(REPO / 'backend' / 'ops' / 'smoke.py'),
         '--checkout', str(checkout), '--db', str(db)],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=300,
    )


def _live(tmp_path, template) -> Path:
    live = tmp_path / 'live.db'
    shutil.copyfile(template, live)
    db = sqlite3.connect(live)
    db.execute('UPDATE settings SET prevent_sleep=1')
    db.commit()
    db.close()
    return live


def test_this_checkout_boots_and_the_live_db_is_untouched(tmp_path, _schema_template):
    live = _live(tmp_path, _schema_template)
    before = live.read_bytes()

    result = _smoke(live, tmp_path)

    assert result.returncode == 0, result.stderr
    assert 'smoke: ok' in result.stdout
    assert live.read_bytes() == before
    # Every default ./data root resolved inside the smoke's own temp dir.
    assert not (tmp_path / 'data').exists()


def test_a_first_install_with_no_database_still_boots(tmp_path):
    assert _smoke(tmp_path / 'absent.db', tmp_path).returncode == 0


def test_a_checkout_that_cannot_start_fails(tmp_path, _schema_template):
    """A duplicated route is the failure this was built for: git merges two
    copies cleanly, and Flask refuses the second one at startup."""
    broken = tmp_path / 'checkout'
    shutil.copytree(REPO / 'backend', broken / 'backend',
                    ignore=shutil.ignore_patterns('tests', '__pycache__', 'smoke.py'))
    health = broken / 'backend' / 'routes' / 'logs.py'
    source = health.read_text()
    first_route = source.index('@bp.')
    route_block = source[first_route:source.index('\n\n\n', first_route)]
    health.write_text(source + '\n\n\n' + route_block + '\n')

    result = _smoke(_live(tmp_path, _schema_template), tmp_path, checkout=broken)

    assert result.returncode == 1
    assert 'overwriting an existing endpoint' in result.stderr


def test_the_copy_cannot_start_a_sleep_inhibitor(tmp_path, _schema_template):
    live = _live(tmp_path, _schema_template)
    copy = tmp_path / 'copy.db'

    copy_db(live, copy)

    read = lambda p: sqlite3.connect(p).execute('SELECT prevent_sleep FROM settings').fetchone()[0]
    assert read(copy) == 0
    assert read(live) == 1
