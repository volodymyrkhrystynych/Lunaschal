"""The deploy watcher's post-restart health wait must not mark a slow-but-alive
boot as failed (a flat 90 s did, for every deploy from Nov 15 to Nov 20), and
must still fail fast when the unit actually crashes.
"""

from backend.ops.health_wait import wait_healthy


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def run(probe_results, states, *, timeout=900):
    clock = FakeClock()
    probes = iter(probe_results)
    state_iter = iter(states)
    last_state = [None]

    def unit_state():
        try:
            last_state[0] = next(state_iter)
        except StopIteration:
            pass
        return last_state[0]

    verdict = wait_healthy(
        lambda: next(probes, False), unit_state,
        timeout=timeout, interval=2, clock=clock, sleep=clock.sleep,
    )
    return verdict, clock.now


def test_healthy_on_first_probe():
    assert run([True], [('active', 'inv1')]) == ('healthy', 0)


def test_slow_boot_past_ninety_seconds_is_healthy():
    # 1f6fcc6c answered after 94 s and was rolled back; this is that boot.
    probes = [False] * 47 + [True]
    verdict, elapsed = run(probes, [('active', 'inv1')])
    assert verdict == 'healthy'
    assert elapsed == 94


def test_unit_that_exits_fails_fast():
    probes = [False] * 100
    states = [('active', 'inv1'), ('active', 'inv1'), ('failed', 'inv1')]
    verdict, elapsed = run(probes, states)
    assert verdict == 'crashed'
    assert elapsed < 10


def test_systemd_restart_loop_counts_as_crash():
    # Restart=always brings the unit straight back as `active`, so ActiveState
    # alone would never notice a crash loop — the invocation id does.
    probes = [False] * 100
    states = [('active', 'inv1'), ('active', 'inv1'), ('activating', 'inv2')]
    verdict, _ = run(probes, states)
    assert verdict == 'restarted'


def test_activating_is_still_waiting():
    probes = [False, False, True]
    states = [('activating', 'inv1')]
    assert run(probes, states)[0] == 'healthy'


def test_hung_but_alive_unit_times_out_at_ceiling():
    verdict, elapsed = run([False] * 10_000, [('active', 'inv1')], timeout=60)
    assert verdict == 'timeout'
    assert 60 <= elapsed <= 62


def test_missing_invocation_id_does_not_trip_restart_check():
    # Older systemd or a parse miss yields ''. Treat that as unknown, not changed.
    probes = [False, False, True]
    states = [('active', ''), ('active', 'inv9')]
    assert run(probes, states)[0] == 'healthy'
