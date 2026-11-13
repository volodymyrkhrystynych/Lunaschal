"""Finding and closing game launchers, straight from /proc.

No psutil: a process's name is `/proc/<pid>/comm` and its first argument is
the first NUL-separated field of `/proc/<pid>/cmdline`, which is all matching
needs. Wine processes show as their Windows exe ("Battle.net.exe"), and
`comm` truncates at 15 characters, so both are checked.

Only processes owned by this server's user are touched — the lock is a
commitment device for one person, not a policy over the machine. `wineserver`
is closed only when a matched Wine launcher was in the same scan: Battle.net
under Lutris leaves it behind and it holds the prefix open, but killing it
blindly would take down any other Wine program too.
"""
import os
import signal
import time
from pathlib import Path

PROC = Path('/proc')
WINE_COMPANIONS = ('wineserver',)
# A game Steam launched runs as its own process, outliving the client. Native
# and Proton games alike carry their install path in their arguments.
PATH_MARKERS = ('/steamapps/common/',)
GRACE_S = 3.0


def _read(path: Path) -> str | None:
    try:
        return path.read_bytes().decode('utf-8', 'replace')
    except OSError:
        return None


def _names(proc_dir: Path) -> tuple[set[str], str]:
    names, path = set(), ''
    comm = _read(proc_dir / 'comm')
    if comm:
        names.add(comm.strip().lower())
    cmdline = _read(proc_dir / 'cmdline')
    if cmdline:
        path = cmdline.replace('\0', ' ').replace('\\', '/').lower()
        first = cmdline.split('\0', 1)[0]
        # A Windows path under Wine uses backslashes.
        base = first.replace('\\', '/').rsplit('/', 1)[-1].strip().lower()
        if base:
            names.add(base)
    return names, path


def _owner(proc_dir: Path) -> int | None:
    try:
        return proc_dir.stat().st_uid
    except OSError:
        return None


def _matches(names: set[str], pattern: str) -> bool:
    pattern = pattern.strip().lower()
    # comm keeps only the first 15 bytes of a name.
    return any(n == pattern or (len(n) == 15 and pattern.startswith(n)) for n in names)


def find(patterns: list[str], *, proc: Path = PROC, uid: int | None = None,
         self_pid: int | None = None) -> list[tuple[int, str]]:
    """(pid, matched pattern) for every running process to close."""
    uid = os.getuid() if uid is None else uid
    self_pid = os.getpid() if self_pid is None else self_pid
    found, wine, companions = [], False, []
    try:
        entries = list(proc.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) == self_pid:
            continue
        if _owner(entry) != uid:
            continue
        names, path = _names(entry)
        if not names:
            continue
        hit = next((p for p in patterns if _matches(names, p)), None)
        if hit is None and any(m in path for m in PATH_MARKERS):
            hit = 'steam game'
        if hit:
            found.append((int(entry.name), hit))
            wine = wine or hit.lower().endswith('.exe') or '.exe' in path
        elif any(_matches(names, c) for c in WINE_COMPANIONS):
            companions.append((int(entry.name), 'wineserver'))
    return found + (companions if wine else [])


def close(pids: list[int], *, kill=os.kill, sleep=time.sleep, alive=None) -> list[int]:
    """SIGTERM, a short grace, then SIGKILL whatever is left. Returns the
    pids that were signalled at all."""
    alive = alive or _alive
    signalled = []
    for pid in pids:
        try:
            kill(pid, signal.SIGTERM)
            signalled.append(pid)
        except (ProcessLookupError, PermissionError):
            continue
    if not signalled:
        return []
    sleep(GRACE_S)
    for pid in signalled:
        if alive(pid):
            try:
                kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    return signalled


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
