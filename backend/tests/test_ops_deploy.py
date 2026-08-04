"""backend/ops/deploy.py must never let auto-deploy touch a feature branch or a
dirty tree — this is what keeps the watcher from clobbering in-progress work on
the desktop machine that doubles as a dev box.
"""

from backend.ops.deploy import needs_deploy


def test_deploys_clean_main_behind_origin():
    assert needs_deploy('main', False, 'aaa', 'bbb') == 'deploy'


def test_skips_feature_branch_even_if_behind():
    assert needs_deploy('feat/whatever', False, 'aaa', 'bbb') == 'skip-branch'


def test_skips_dirty_main_even_if_behind():
    assert needs_deploy('main', True, 'aaa', 'bbb') == 'skip-dirty'


def test_dirty_feature_branch_reports_branch_reason_first():
    assert needs_deploy('feat/whatever', True, 'aaa', 'bbb') == 'skip-branch'


def test_up_to_date_clean_main():
    assert needs_deploy('main', False, 'aaa', 'aaa') == 'up-to-date'


def test_up_to_date_wins_over_dirty_check():
    # Nothing to pull, so dirty-tree state is irrelevant either way.
    assert needs_deploy('main', True, 'aaa', 'aaa') == 'up-to-date'


def test_local_commit_ahead_of_origin_is_not_a_deploy():
    # An unpushed merge on main: the shas differ, but origin has nothing new.
    # Reporting 'deploy' here makes deploy-check.sh pull nothing and then
    # rebuild + restart the production window on every 5-minute tick.
    assert needs_deploy('main', False, 'bbb', 'aaa', local_ahead=True) == 'ahead'


def test_ahead_reported_even_when_tree_is_dirty():
    assert needs_deploy('main', True, 'bbb', 'aaa', local_ahead=True) == 'ahead'


def test_behind_origin_still_deploys_when_not_ahead():
    assert needs_deploy('main', False, 'aaa', 'bbb', local_ahead=False) == 'deploy'


def test_ahead_on_a_feature_branch_still_reports_branch_first():
    assert needs_deploy('feat/whatever', False, 'bbb', 'aaa', local_ahead=True) == 'skip-branch'
