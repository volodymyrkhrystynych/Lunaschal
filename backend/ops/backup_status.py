"""Pure logic behind the Settings → Backup panel.

The nightly job (`ops/backup.sh`) deliberately *skips* rather than fails when
the destination is missing — a drive is unplugged often enough that a hard
failure every night would train you to ignore it. The cost of that choice is
that a destination which goes missing permanently looks exactly like one that
is missing for a night: systemd reports success either way. That is how this
project's backups sat dead for nineteen days after the drive moved from a
udisks auto-mount to an fstab one and `BACKUP_HDD_PATH` was never updated.

So the panel does not re-litigate the skip-don't-fail decision. It watches the
*evidence on the drive* instead: how old the newest dated DB snapshot is. A
backup that has not landed in days is a problem regardless of how cheerfully
each individual run exited, and that is the one signal that would have caught
the failure above.

Everything here is filesystem-free and clock-free (today is passed in) so the
classification can be tested without a 7TB drive attached.
"""

from datetime import date, datetime, timedelta

DATE_FORMAT = '%Y-%m-%d'

# A backup older than this is called out. Two days rather than one: the job runs
# at 03:00, so a single missed night (asleep, drive briefly unplugged) is normal
# and should not cry wolf. Three consecutive misses is a real signal.
STALE_AFTER_DAYS = 2


def parse_env(text: str) -> dict[str, str]:
    """Read `ops/backup.env`'s `KEY=value` lines.

    Deliberately not a shell parser — the file is sourced by bash at run time,
    but the panel only needs to *report* what the job will read. Handles the
    comment and quoting forms actually used in backup.env.example; anything
    fancier (command substitution, line continuations) would be a lie to
    display as a resolved value, so it is left as the raw text.
    """
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        key, sep, value = line.partition('=')
        if not sep:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value
    return result


def snapshot_dates(names: list[str]) -> list[str]:
    """The subdirectory names under `db/` that are real dates, oldest first.

    Anything else in there (a stray file, a half-written temp dir) is ignored
    rather than reported as a snapshot — the same tolerance
    `backend.ops.backup.prune_candidates` applies before deleting.
    """
    dates = []
    for raw in names:
        try:
            dates.append(datetime.strptime(raw, DATE_FORMAT).date())
        except ValueError:
            continue
    return [d.strftime(DATE_FORMAT) for d in sorted(dates)]


def snapshot_age_days(latest: str | None, today: date) -> int | None:
    """Whole days between the newest snapshot and today. None if there are none.

    Negative ages (a snapshot dated in the future, which a clock skew or a
    timezone-confused run can produce) are clamped to 0 rather than reported as
    a fresher-than-possible backup.
    """
    if not latest:
        return None
    try:
        d = datetime.strptime(latest, DATE_FORMAT).date()
    except ValueError:
        return None
    return max(0, (today - d).days)


def expected_snapshot_count(dates: list[str], keep_days: int, today: date) -> int:
    """How many snapshots *should* be on the drive given the retention window.

    Used only to explain a low count in the UI: with `keep_days=14` a healthy
    drive holds 14, but a destination that has only been backing up for three
    days legitimately holds 3. Comparing the count to this instead of to
    `keep_days` keeps a young backup from being flagged as broken.
    """
    if not dates:
        return 0
    oldest = datetime.strptime(dates[0], DATE_FORMAT).date()
    days_running = (today - oldest).days + 1
    return max(0, min(keep_days, days_running))


def classify_health(
    *,
    configured: bool,
    mount_present: bool,
    writable: bool,
    latest_snapshot: str | None,
    today: date,
    mount_readonly: bool = False,
    stale_after_days: int = STALE_AFTER_DAYS,
) -> tuple[str, list[str]]:
    """Reduce the destination's state to one status plus human-readable problems.

    Ordered worst-first and returns on the first hit, because the conditions
    nest: an unreachable drive has no snapshots to be stale, and a read-only one
    cannot gain new ones. Reporting the deepest cause rather than the shallowest
    symptom is what makes the panel actionable — "snapshots are 19 days old" is
    a symptom, "the configured path does not exist" is the fix.
    """
    if not configured:
        return 'unconfigured', [
            'No BACKUP_HDD_PATH set in ops/backup.env — the nightly job has no '
            'destination and backs nothing up.'
        ]

    if not mount_present:
        return 'unreachable', [
            'The configured destination is not present. If the drive is simply '
            'unplugged this is harmless; if it stays this way the nightly job '
            'silently skips every run.'
        ]

    # "Cannot write here" has two very different causes with two different
    # fixes, and conflating them sends you to the wrong one. A genuinely
    # read-only mount (ST_RDONLY — a dirty volume, a failing disk,
    # errors=remount-ro having fired) needs an fsck and a remount. A mount that
    # is rw but still refuses the write is an *ownership* problem, which on a
    # filesystem with no POSIX ownership of its own — exFAT, FAT, NTFS — means
    # the mount options are synthesizing the wrong uid.
    #
    # That second case is the one that actually happened here, and it is easy to
    # misread as the first: udisks mounts removable media with uid= set to the
    # mounting user, but a hand-written fstab line without uid=/gid= defaults to
    # root, so moving the drive to fstab silently made it unwritable for the
    # user the backup timer runs as.
    if mount_readonly:
        return 'readonly', [
            'The destination is mounted read-only, so the nightly job cannot '
            'write to it. Usually a dirty volume: unmount it, run fsck, and '
            'mount it again. Not something the app can fix.'
        ]

    if not writable:
        return 'permissions', [
            'The destination is mounted read-write, but this user cannot write '
            'to it — so the nightly job gets "Permission denied". On exFAT/NTFS '
            'this is almost always missing uid=/gid= mount options, which leave '
            'every file owned by root. Check the fstab entry for the drive.'
        ]

    age = snapshot_age_days(latest_snapshot, today)
    if age is None:
        return 'empty', [
            'The drive is reachable and writable, but holds no dated DB '
            'snapshot yet. The next scheduled run should create one.'
        ]

    if age > stale_after_days:
        return 'stale', [
            f'The newest DB snapshot is {age} days old. The drive is reachable '
            f'and writable, so the job is failing for some other reason — check '
            f'`journalctl --user -u lunaschal-backup`.'
        ]

    return 'ok', []


def parse_systemctl_show(text: str) -> dict[str, str]:
    """`systemctl show -p A -p B` output: one `Key=value` per line.

    Kept separate from `parse_env` even though the line format looks identical:
    systemd never quotes these values, and an empty value is meaningful (the
    property exists but is unset), where in an env file it would mean the
    setting was blanked deliberately.
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition('=')
        if sep and key.strip():
            result[key.strip()] = value.strip()
    return result


def parse_systemd_timestamp(value: str) -> str | None:
    """systemd's `Day YYYY-MM-DD HH:MM:SS TZ` → ISO 8601, or None.

    systemd writes `n/a` (and, for a unit that has never run, an empty string)
    where a timestamp would go; both must come back as None rather than as a
    string the frontend would try to render as a date.
    """
    value = value.strip()
    if not value or value == 'n/a':
        return None
    # Drop the leading weekday and the trailing zone abbreviation, neither of
    # which strptime can consume portably.
    parts = value.split()
    if len(parts) < 3:
        return None
    try:
        stamp = datetime.strptime(f'{parts[1]} {parts[2]}', '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None
    return stamp.isoformat()


def next_run_estimate(last_run_iso: str | None, now: datetime, hour: int = 3) -> str:
    """Best-effort next-run time for when systemd cannot be queried.

    The timer's own `NextElapseUSecRealtime` is authoritative and preferred; this
    is the fallback for a Flask process that has no user bus (a dev run under a
    plain shell, say), so the panel can still say roughly when to expect the next
    backup instead of showing nothing.
    """
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.isoformat()
