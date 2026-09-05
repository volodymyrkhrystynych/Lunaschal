"""Merging the client's live view with our rows, and the retention arithmetic.

Both pure — no client, no database, no clock.
"""
import pytest

from backend.torrent import merge, retention


def live(**over):
    base = {
        'hash': 'AABB', 'name': 'Debian ISO', 'state': 'downloading',
        'progress': 0.5, 'size': 100, 'downloaded': 50, 'uploaded': 0,
        'ratio': 0.0, 'dlspeed': 10, 'upspeed': 0, 'eta': 120,
        'num_seeds': 3, 'num_leechs': 1, 'category': 'linux',
        'save_path': '/downloads', 'content_path': '/downloads/Debian ISO',
        'added_on': 1000, 'completion_on': 0,
        'ratio_limit': -2, 'seeding_time_limit': -2, 'dl_limit': 0, 'up_limit': 0,
    }
    base.update(over)
    return base


def row(**over):
    base = {'id': 'ULID1', 'info_hash': 'aabb', 'name': 'Debian ISO',
            'note': 'for the server', 'retention_days': 7, 'completed_at': None}
    base.update(over)
    return base


def test_our_row_decorates_the_live_torrent():
    [out] = merge.merge([live()], [row()])
    assert out['note'] == 'for the server'
    assert out['retentionDays'] == 7
    assert out['tracked'] is True
    assert out['id'] == 'ULID1'


def test_hash_matching_is_case_insensitive():
    """qBittorrent reports lowercase; magnets carry either. A mismatch here
    would silently show every torrent as untracked."""
    [out] = merge.merge([live(hash='AABB')], [row(info_hash='aabb')])
    assert out['tracked'] is True


def test_a_torrent_added_outside_lunaschal_still_shows():
    [out] = merge.merge([live()], [])
    assert out['tracked'] is False
    assert out['note'] is None
    assert out['name'] == 'Debian ISO'


def test_infinite_eta_becomes_none():
    """qBittorrent's 8640000 sentinel renders as a very confident '100 days'."""
    assert merge.present(live(eta=8640000))['eta'] is None
    assert merge.present(live(eta=0))['eta'] is None
    assert merge.present(live(eta=120))['eta'] == 120


def test_unset_completion_becomes_none():
    assert merge.present(live(completion_on=0))['completedAt'] is None
    assert merge.present(live(completion_on=-1))['completedAt'] is None
    assert merge.present(live(completion_on=1700))['completedAt'] == 1700


def test_global_and_unlimited_share_limits_both_read_as_unset():
    assert merge.present(live(ratio_limit=-2))['ratioLimit'] is None
    assert merge.present(live(ratio_limit=-1))['ratioLimit'] is None
    assert merge.present(live(ratio_limit=2.0))['ratioLimit'] == 2.0


@pytest.mark.parametrize('state,group', [
    ('downloading', 'downloading'), ('metaDL', 'downloading'),
    ('uploading', 'seeding'), ('stalledUP', 'seeding'),
    ('pausedDL', 'paused'), ('stoppedDL', 'paused'),
    ('pausedUP', 'complete'), ('stoppedUP', 'complete'),
    ('stalledDL', 'stalled'), ('checkingDL', 'checking'),
    ('queuedDL', 'queued'), ('error', 'error'), ('missingFiles', 'error'),
])
def test_state_grouping_covers_both_qbittorrent_4_and_5_spellings(state, group):
    assert merge.state_group(state) == group


def test_an_unknown_state_is_treated_as_an_error_not_as_healthy():
    assert merge.state_group('somethingNew') == 'error'


def test_sorted_newest_first():
    out = merge.merge([live(hash='a', added_on=1), live(hash='b', added_on=9)], [])
    assert [t['infoHash'] for t in out] == ['b', 'a']


def test_orphaned_rows_are_the_ones_the_client_forgot():
    rows = [row(info_hash='aabb'), row(info_hash='ccdd')]
    assert merge.orphaned_hashes([live(hash='AABB')], rows) == ['ccdd']


# --- retention -------------------------------------------------------------

DAY = 86400


def test_retention_is_off_unless_the_row_opts_in():
    assert not retention.is_due({'retention_days': 0, 'completed_at': 1}, now=10 ** 9)
    assert not retention.is_due({'retention_days': None, 'completed_at': 1}, now=10 ** 9)


def test_an_unfinished_torrent_never_ages_out():
    """Measured from completion, not from when it was added — a download that
    took a week has not been *kept* for a week."""
    assert not retention.is_due({'retention_days': 1, 'completed_at': None}, now=10 ** 9)


def test_due_exactly_on_the_boundary_day():
    completed = 1_000_000
    row_ = {'retention_days': 7, 'completed_at': completed}
    assert not retention.is_due(row_, now=completed + 7 * DAY - 1)
    assert retention.is_due(row_, now=completed + 7 * DAY)


def test_purge_selects_only_the_due_ones():
    now = 1_000_000 + 30 * DAY
    rows = [
        {'id': 'a', 'retention_days': 7, 'completed_at': 1_000_000},
        {'id': 'b', 'retention_days': 0, 'completed_at': 1_000_000},
        {'id': 'c', 'retention_days': 90, 'completed_at': 1_000_000},
    ]
    assert [r['id'] for r in retention.torrents_to_purge(rows, now)] == ['a']
