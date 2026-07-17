"""Tests for the GPU VRAM startup snapshot. measure_base_gpu_vram() is called
from create_app() on every app startup (including once per test's `client`
fixture, against the real nvidia-smi), so route tests set the module-level
cache directly rather than relying on ordering between fixture setup and
monkeypatch — that keeps them deterministic regardless of what's actually
measured on the machine running the suite."""
from types import SimpleNamespace

import pytest

from backend.routes import settings


@pytest.fixture(autouse=True)
def restore_gpu_vram_cache():
    prev = (settings._gpu_base_vram_mb, settings._gpu_total_vram_mb)
    yield
    settings._gpu_base_vram_mb, settings._gpu_total_vram_mb = prev


def test_measure_base_gpu_vram_caches_used_and_total(monkeypatch):
    settings._gpu_base_vram_mb = None
    settings._gpu_total_vram_mb = None

    def fake_run(*args, **kwargs):
        return SimpleNamespace(stdout='3303, 8192\n')

    monkeypatch.setattr(settings.subprocess, 'run', fake_run)
    settings.measure_base_gpu_vram()

    assert settings._gpu_base_vram_mb == 3303
    assert settings._gpu_total_vram_mb == 8192


def test_measure_base_gpu_vram_only_runs_once_per_process(monkeypatch):
    settings._gpu_base_vram_mb = 999
    settings._gpu_total_vram_mb = 1234
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(stdout='1, 2\n')

    monkeypatch.setattr(settings.subprocess, 'run', fake_run)
    settings.measure_base_gpu_vram()

    assert calls == []  # already measured — skipped, real values untouched
    assert settings._gpu_base_vram_mb == 999
    assert settings._gpu_total_vram_mb == 1234


def test_measure_base_gpu_vram_leaves_unset_when_nvidia_smi_missing(monkeypatch):
    settings._gpu_base_vram_mb = None
    settings._gpu_total_vram_mb = None

    def fake_run(*args, **kwargs):
        raise FileNotFoundError('nvidia-smi not found')

    monkeypatch.setattr(settings.subprocess, 'run', fake_run)
    settings.measure_base_gpu_vram()

    assert settings._gpu_base_vram_mb is None
    assert settings._gpu_total_vram_mb is None


def test_measure_base_gpu_vram_leaves_unset_on_malformed_output(monkeypatch):
    settings._gpu_base_vram_mb = None
    settings._gpu_total_vram_mb = None

    def fake_run(*args, **kwargs):
        return SimpleNamespace(stdout='not a number\n')

    monkeypatch.setattr(settings.subprocess, 'run', fake_run)
    settings.measure_base_gpu_vram()

    assert settings._gpu_base_vram_mb is None
    assert settings._gpu_total_vram_mb is None


def test_get_settings_defaults_nudge_enabled_and_45_minute_interval(client):
    resp = client.get('/api/settings')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['nudgeEnabled'] is True
    assert data['nudgeIntervalMinutes'] == 45


def test_patch_settings_updates_nudge_fields(client):
    resp = client.patch('/api/settings/ai', json={'nudgeEnabled': False, 'nudgeIntervalMinutes': 20})
    assert resp.status_code == 200

    data = client.get('/api/settings').get_json()
    assert data['nudgeEnabled'] is False
    assert data['nudgeIntervalMinutes'] == 20


def test_gpu_vram_route_serves_cached_snapshot(client):
    settings._gpu_base_vram_mb = 3303
    settings._gpu_total_vram_mb = 8192

    resp = client.get('/api/settings/gpu-vram')
    assert resp.status_code == 200
    assert resp.get_json() == {'available': True, 'baseMb': 3303, 'totalMb': 8192}


def test_gpu_vram_route_unavailable_when_not_measured(client):
    settings._gpu_base_vram_mb = None
    settings._gpu_total_vram_mb = None

    resp = client.get('/api/settings/gpu-vram')
    assert resp.status_code == 200
    assert resp.get_json() == {'available': False}


@pytest.fixture(autouse=True)
def restore_sleep_inhibitor():
    prev = settings._sleep_inhibitor
    yield
    settings._sleep_inhibitor = prev


def test_kill_orphaned_inhibitors_kills_matching_pids(monkeypatch):
    monkeypatch.setattr(
        settings.subprocess, 'run',
        lambda *a, **k: SimpleNamespace(stdout='111\n222\n'),
    )
    killed = []
    monkeypatch.setattr(settings.os, 'kill', lambda pid, sig: killed.append(pid))

    settings._kill_orphaned_inhibitors()

    assert killed == [111, 222]


def test_kill_orphaned_inhibitors_tolerates_missing_pgrep(monkeypatch):
    def fake_run(*a, **k):
        raise FileNotFoundError('pgrep not found')

    monkeypatch.setattr(settings.subprocess, 'run', fake_run)

    settings._kill_orphaned_inhibitors()  # must not raise


def test_kill_orphaned_inhibitors_tolerates_already_dead_pid(monkeypatch):
    monkeypatch.setattr(
        settings.subprocess, 'run',
        lambda *a, **k: SimpleNamespace(stdout='333\n'),
    )

    def fake_kill(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(settings.os, 'kill', fake_kill)

    settings._kill_orphaned_inhibitors()  # must not raise


def test_set_sleep_inhibitor_enable_sweeps_orphans_then_spawns(monkeypatch):
    swept = []
    monkeypatch.setattr(settings, '_kill_orphaned_inhibitors', lambda: swept.append(1))
    spawned = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(settings.subprocess, 'Popen', lambda *a, **k: spawned)

    settings._set_sleep_inhibitor(True)

    assert swept == [1]
    assert settings._sleep_inhibitor is spawned


def test_set_sleep_inhibitor_disable_sweeps_orphans_and_clears_handle(monkeypatch):
    swept = []
    monkeypatch.setattr(settings, '_kill_orphaned_inhibitors', lambda: swept.append(1))
    settings._sleep_inhibitor = SimpleNamespace(poll=lambda: None, terminate=lambda: None)

    settings._set_sleep_inhibitor(False)

    assert swept == [1]
    assert settings._sleep_inhibitor is None
