"""Echo-cancellation tests for the meeting recorder, with pactl faked."""
import time

import pytest

from backend.meetings import recorder


class FakePopen:
    def __init__(self):
        self.returncode = None
        self.stdin = None

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.returncode = 0

    def wait(self, timeout=None):
        if self.returncode is None:
            raise RuntimeError('would block')
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = 0


class FakePactl:
    """Simulates the pactl subcommands the recorder uses."""

    def __init__(self):
        self.calls: list[tuple[str, ...]] = []
        self.fail_load = False
        self.default_sink = 'real_sink'
        self.modules: dict[str, str] = {}  # id -> args line
        self._next_module_id = 50

    def __call__(self, *args):
        self.calls.append(args)
        cmd = args[0]
        if cmd == 'get-default-sink':
            return self.default_sink
        if cmd == 'load-module':
            if self.fail_load:
                raise RuntimeError('Module initialization failed')
            module_id = str(self._next_module_id)
            self._next_module_id += 1
            self.modules[module_id] = '\t'.join((module_id, args[1], ' '.join(args[2:])))
            return module_id
        if cmd == 'unload-module':
            self.modules.pop(args[1], None)
            return ''
        if cmd == 'list' and args[1:] == ('short', 'modules'):
            return '\n'.join(self.modules.values())
        if cmd == 'list' and args[1:] == ('short', 'sources'):
            has_ec = any('module-echo-cancel' in line for line in self.modules.values())
            return f'5\t{recorder.EC_SOURCE}\t...' if has_ec else '3\tmic\t...'
        raise AssertionError(f'unexpected pactl call: {args}')


@pytest.fixture
def rec(client, monkeypatch, tmp_path):
    monkeypatch.setenv('MEETINGS_ROOT', str(tmp_path / 'meetings'))
    monkeypatch.setattr(recorder, '_active', None)
    monkeypatch.setattr(time, 'sleep', lambda s: None)
    pactl = FakePactl()
    monkeypatch.setattr(recorder, '_pactl', pactl)
    spawned = []

    def fake_spawn(pulse_input, out_path):
        p = FakePopen()
        spawned.append(pulse_input)
        return p

    monkeypatch.setattr(recorder, '_spawn_ffmpeg', fake_spawn)
    return {'pactl': pactl, 'spawned': spawned, 'client': client}


def _set_echo_cancel(enabled):
    from backend.db.connection import get_db
    get_db().execute('UPDATE settings SET meeting_echo_cancel=? WHERE id=1', (1 if enabled else 0,))
    get_db().commit()


def test_disabled_records_raw_mic_with_no_ec_calls(rec):
    _set_echo_cancel(False)
    recorder.start('m1')
    assert rec['spawned'] == ['default', 'real_sink.monitor']
    assert not any('load-module' in c for c in rec['pactl'].calls)
    recorder.stop('m1')


def test_enabled_records_from_ec_source_in_monitor_mode(rec):
    _set_echo_cancel(True)
    recorder.start('m1')
    assert rec['spawned'] == [recorder.EC_SOURCE, 'real_sink.monitor']
    calls = rec['pactl'].calls
    load_call = next(c for c in calls if c[0] == 'load-module')
    assert 'monitor.mode=true' in load_call
    assert not any(c[0] in ('set-default-sink', 'move-sink-input') for c in calls)


def test_stop_unloads_module_without_touching_playback(rec):
    _set_echo_cancel(True)
    recorder.start('m1')
    assert rec['pactl'].modules  # module loaded
    recorder.stop('m1')
    assert rec['pactl'].modules == {}  # unloaded
    calls = rec['pactl'].calls
    assert not any(c[0] in ('set-default-sink', 'move-sink-input') for c in calls)


def test_module_load_failure_falls_back_to_raw_mic(rec):
    _set_echo_cancel(True)
    rec['pactl'].fail_load = True
    recorder.start('m1')  # must not raise
    assert rec['spawned'] == ['default', 'real_sink.monitor']
    recorder.stop('m1')


def test_orphaned_module_cleaned_up_on_next_start(rec):
    _set_echo_cancel(True)
    # A previous crashed instance left our module loaded.
    rec['pactl'].modules['9'] = f'9\tmodule-echo-cancel\tsource_name={recorder.EC_SOURCE}'
    recorder.start('m1')
    assert ('unload-module', '9') in rec['pactl'].calls
    assert '9' not in rec['pactl'].modules


def test_ffmpeg_failure_tears_down_echo_cancel(rec, monkeypatch):
    _set_echo_cancel(True)

    def bad_spawn(pulse_input, out_path):
        p = FakePopen()
        p.returncode = 1  # exits immediately
        return p

    monkeypatch.setattr(recorder, '_spawn_ffmpeg', bad_spawn)
    with pytest.raises(RuntimeError):
        recorder.start('m1')
    assert rec['pactl'].modules == {}  # EC module not leaked


def test_settings_toggle_round_trip(client):
    assert client.get('/api/settings').get_json()['meetingEchoCancel'] is False
    client.patch('/api/settings/ai', json={'meetingEchoCancel': True})
    assert client.get('/api/settings').get_json()['meetingEchoCancel'] is True
