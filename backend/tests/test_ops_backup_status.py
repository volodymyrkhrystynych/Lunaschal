"""backend/ops/backup_status.py: the classification behind Settings → Backup.

The case every test here is really about is the one that motivated the panel —
a destination path that went stale, so the nightly job skipped the drive and
exited 0 for nineteen days while systemd reported nothing but success.
"""

from datetime import date, datetime

from backend.ops.backup_status import (
    classify_health,
    expected_snapshot_count,
    next_run_estimate,
    parse_env,
    parse_systemctl_show,
    parse_systemd_timestamp,
    snapshot_age_days,
    snapshot_dates,
)

TODAY = date(2026, 8, 22)


def test_parse_env_reads_the_real_backup_env_forms():
    text = '\n'.join([
        '# Local backup config (gitignored).',
        '',
        'BACKUP_HDD_PATH=/media/expansion/lunaschal',
        'BACKUP_TABLET_HOST=',
        'export BACKUP_TABLET_USER="volodya"',
        "BACKUP_TABLET_PATH='/home/volodya/lunaschal-backups'",
        'BACKUP_RETENTION_DAYS=14   ',
        'not-an-assignment',
    ])

    assert parse_env(text) == {
        'BACKUP_HDD_PATH': '/media/expansion/lunaschal',
        'BACKUP_TABLET_HOST': '',
        'BACKUP_TABLET_USER': 'volodya',
        'BACKUP_TABLET_PATH': '/home/volodya/lunaschal-backups',
        'BACKUP_RETENTION_DAYS': '14',
    }


def test_parse_env_keeps_a_value_containing_equals_intact():
    # An rsync flag or an ssh option would otherwise be truncated at the first
    # '=' and displayed as something the job does not actually use.
    assert parse_env('OPTS=--rsh=ssh -p 2222')['OPTS'] == '--rsh=ssh -p 2222'


def test_snapshot_dates_sorts_and_ignores_non_dates():
    names = ['2026-08-03', 'lost+found', '2026-07-29', 'tmp-partial', '2026-08-01']
    assert snapshot_dates(names) == ['2026-07-29', '2026-08-01', '2026-08-03']


def test_snapshot_dates_rejects_impossible_dates():
    # A directory literally named '2026-02-30' is not a snapshot, and counting
    # it would make an empty backup look populated.
    assert snapshot_dates(['2026-02-30', '2026-13-01']) == []


def test_snapshot_age_days_measures_from_the_newest():
    assert snapshot_age_days('2026-08-03', TODAY) == 19
    assert snapshot_age_days('2026-08-22', TODAY) == 0
    assert snapshot_age_days(None, TODAY) is None


def test_snapshot_age_days_clamps_a_future_snapshot_to_zero():
    # Clock skew must not render as a negative age the UI would show as
    # "-3 days old".
    assert snapshot_age_days('2026-08-25', TODAY) == 0


def test_unconfigured_destination_is_reported_before_anything_else():
    health, problems = classify_health(
        configured=False, mount_present=False, writable=False,
        latest_snapshot=None, today=TODAY,
    )
    assert health == 'unconfigured'
    assert 'BACKUP_HDD_PATH' in problems[0]


def test_missing_mount_reports_unreachable_not_stale():
    """The exact nineteen-day failure: snapshots are ancient *because* the
    configured path does not exist. Naming the symptom would send you looking
    at the wrong thing, so the deeper cause has to win.
    """
    health, problems = classify_health(
        configured=True, mount_present=False, writable=False,
        latest_snapshot='2026-08-03', today=TODAY,
    )
    assert health == 'unreachable'
    assert 'not present' in problems[0]


def test_read_only_mount_reports_readonly_not_stale():
    health, problems = classify_health(
        configured=True, mount_present=True, writable=False,
        mount_readonly=True, latest_snapshot='2026-08-03', today=TODAY,
    )
    assert health == 'readonly'
    assert 'fsck' in problems[0]


def test_unwritable_but_read_write_mount_is_a_permissions_problem():
    """The condition that was actually live on this machine.

    The mount is rw; the drive is simply owned by root because the fstab entry
    has no uid=. os.access() reports False for this and for a genuinely
    read-only mount alike, but the fixes are unrelated — telling the user to run
    fsck here would waste their time on a healthy filesystem.
    """
    health, problems = classify_health(
        configured=True, mount_present=True, writable=False,
        mount_readonly=False, latest_snapshot='2026-08-03', today=TODAY,
    )
    assert health == 'permissions'
    assert 'uid=' in problems[0]
    assert 'fsck' not in problems[0]


def test_reachable_writable_drive_with_no_snapshots_is_empty_not_stale():
    health, _ = classify_health(
        configured=True, mount_present=True, writable=True,
        latest_snapshot=None, today=TODAY,
    )
    assert health == 'empty'


def test_stale_only_when_the_drive_is_otherwise_healthy():
    health, problems = classify_health(
        configured=True, mount_present=True, writable=True,
        latest_snapshot='2026-08-03', today=TODAY,
    )
    assert health == 'stale'
    assert '19 days old' in problems[0]


def test_one_missed_night_is_not_stale():
    """The job runs at 03:00 and the machine is a desktop that sleeps. A single
    gap is normal; flagging it would train the user to ignore the warning.
    """
    for age_days in (0, 1, 2):
        latest = date(2026, 8, 22 - age_days).strftime('%Y-%m-%d')
        health, problems = classify_health(
            configured=True, mount_present=True, writable=True,
            latest_snapshot=latest, today=TODAY,
        )
        assert health == 'ok', f'{age_days} days should not be stale'
        assert problems == []


def test_three_missed_nights_is_stale():
    health, _ = classify_health(
        configured=True, mount_present=True, writable=True,
        latest_snapshot='2026-08-19', today=TODAY,
    )
    assert health == 'stale'


def test_expected_count_does_not_flag_a_young_backup():
    # Three days of snapshots under a 14-day retention is correct, not a
    # backup that lost eleven.
    dates = ['2026-08-20', '2026-08-21', '2026-08-22']
    assert expected_snapshot_count(dates, keep_days=14, today=TODAY) == 3


def test_expected_count_caps_at_the_retention_window():
    dates = ['2026-01-01', '2026-08-22']
    assert expected_snapshot_count(dates, keep_days=14, today=TODAY) == 14


def test_expected_count_of_an_empty_drive_is_zero():
    assert expected_snapshot_count([], keep_days=14, today=TODAY) == 0


def test_parse_systemctl_show_keeps_empty_values():
    # An unset property is meaningfully different from an absent one.
    props = parse_systemctl_show('Result=success\nExecMainStartTimestamp=\n')
    assert props == {'Result': 'success', 'ExecMainStartTimestamp': ''}


def test_parse_systemd_timestamp_converts_to_iso():
    assert parse_systemd_timestamp('Sat 2026-08-22 03:00:10 EDT') == '2026-08-22T03:00:10'


def test_parse_systemd_timestamp_handles_never_run_units():
    assert parse_systemd_timestamp('n/a') is None
    assert parse_systemd_timestamp('') is None
    assert parse_systemd_timestamp('garbage') is None


def test_next_run_estimate_rolls_past_todays_window():
    after = next_run_estimate(None, datetime(2026, 8, 22, 14, 0, 0))
    assert after == '2026-08-23T03:00:00'

    before = next_run_estimate(None, datetime(2026, 8, 22, 1, 0, 0))
    assert before == '2026-08-22T03:00:00'
