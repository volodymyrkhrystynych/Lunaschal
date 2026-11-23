"""Wait for a just-restarted lunaschal.service to answer `/api/health`.

The auto-deploy watcher used to give it a flat 90 seconds. Startup now runs
the `_ensure_*` migrations and the startup backfills against the live DB, and
on the production box that takes anywhere from under a minute to several — so
healthy commits (1f6fcc6c answered at 94 s) were recorded as failed, rolled
back, and then blocked as `skip-failed` until the next commit landed, where the
same thing happened again.

A wall-clock limit can't tell "still starting" from "hung", but systemd can
tell "still running" from "crashed": so this waits for as long as the *same
invocation* of the unit stays up (bounded by a generous ceiling), and fails at
once if the unit stops or systemd restarts it — a commit that crashes on
startup is still rolled back within seconds.

`wait_healthy` is pure over its three probes so the policy is unit tested;
`main()` wires in curl-free HTTP and `systemctl --user show`.
"""

import ssl
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from typing import Literal

Verdict = Literal['healthy', 'crashed', 'restarted', 'timeout']

# Ceiling, not the expected time. Measured production boots ran 50 s – 5 min.
DEFAULT_TIMEOUT = 15 * 60
DEFAULT_INTERVAL = 2.0

# `activating` covers Type=notify/forking units mid-start; lunaschal.service is
# Type=simple and goes straight to `active`, but either means "still trying".
_RUNNING_STATES = ('active', 'activating', 'reloading')


def wait_healthy(
    probe: Callable[[], bool],
    unit_state: Callable[[], tuple[str, str]],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = DEFAULT_INTERVAL,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Verdict:
    """`unit_state()` returns `(ActiveState, InvocationID)`. The invocation id
    seen on the first call is the one being waited on; a different one means
    systemd restarted the process (Restart=always), i.e. it crashed."""
    deadline = clock() + timeout
    _, invocation = unit_state()
    while True:
        if probe():
            return 'healthy'
        state, current = unit_state()
        if state not in _RUNNING_STATES:
            return 'crashed'
        if invocation and current != invocation:
            return 'restarted'
        if clock() >= deadline:
            return 'timeout'
        sleep(interval)


def _http_probe(port: int) -> Callable[[], bool]:
    # Production serves a self-signed cert on 127.0.0.1; dev-style setups serve
    # plain HTTP. Mirrors the two curl calls this replaced.
    insecure = ssl.create_default_context()
    insecure.check_hostname = False
    insecure.verify_mode = ssl.CERT_NONE

    def probe() -> bool:
        for scheme, ctx in (('https', insecure), ('http', None)):
            try:
                with urllib.request.urlopen(
                    f'{scheme}://127.0.0.1:{port}/api/health', timeout=3, context=ctx
                ) as resp:
                    if 200 <= resp.status < 300:
                        return True
            except Exception:
                continue
        return False

    return probe


def _systemd_state(unit: str) -> Callable[[], tuple[str, str]]:
    def state() -> tuple[str, str]:
        out = subprocess.run(
            ['systemctl', '--user', 'show', unit, '-p', 'ActiveState', '-p', 'InvocationID'],
            capture_output=True, text=True, check=False,
        ).stdout
        props = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
        return props.get('ActiveState', ''), props.get('InvocationID', '')

    return state


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--unit', default='lunaschal.service')
    parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()

    started = time.monotonic()
    verdict = wait_healthy(_http_probe(args.port), _systemd_state(args.unit), timeout=args.timeout)
    print(f'{verdict} after {time.monotonic() - started:.0f}s')
    sys.exit(0 if verdict == 'healthy' else 1)


if __name__ == '__main__':
    main()
