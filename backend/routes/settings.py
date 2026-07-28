import json
import os
import random
import signal
import subprocess
import time
import urllib.request
from flask import Blueprint, jsonify, request
from backend.auth import NETWORK_MODE
from backend.ai.llm import REASONING_EFFORTS
from backend.db.connection import build_update, get_db

_sleep_inhibitor: subprocess.Popen | None = None
_INHIBIT_WHO = 'Lunaschal'


def _kill_orphaned_inhibitors() -> None:
    """Kill any systemd-inhibit processes tagged with our --who marker.

    The Werkzeug --debug reloader (and crashes) can kill this process
    without running atexit handlers, orphaning the systemd-inhibit child
    (reparented to init) and leaking a permanent sleep-block lock that a
    later process's in-memory _sleep_inhibitor handle can't see or clear.
    Sweeping by command line instead of relying on that handle lets us
    clean up locks left behind by prior process instances too.
    """
    try:
        out = subprocess.run(
            ['pgrep', '-f', f'systemd-inhibit --what=sleep:idle --who={_INHIBIT_WHO}'],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return
    for pid in out.stdout.split():
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (ValueError, ProcessLookupError):
            pass


def _set_sleep_inhibitor(enabled: bool) -> None:
    global _sleep_inhibitor
    _kill_orphaned_inhibitors()
    if enabled:
        _sleep_inhibitor = subprocess.Popen(
            ['systemd-inhibit', '--what=sleep:idle', f'--who={_INHIBIT_WHO}',
             '--why=Server mode active', '--mode=block', 'sleep', 'infinity'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        _sleep_inhibitor = None

bp = Blueprint('settings', __name__, url_prefix='/api/settings')


def _get_settings():
    row = get_db().execute('SELECT * FROM settings LIMIT 1').fetchone()
    return dict(row) if row else None


@bp.get('')
def get_settings():
    s = _get_settings()
    if not s:
        return jsonify(None)
    return jsonify({
        'ollamaUrl': s.get('ollama_url'),
        'ollamaModel': s.get('ollama_model'),
        'llmReasoningEffort': s.get('llm_reasoning_effort') or 'none',
        'llmMaxTokens': s.get('llm_max_tokens') or 4096,
        'llmNumCtx': s.get('llm_num_ctx') or 4096,
        'hasHfToken': bool(s.get('hf_token')),
        'networkMode': NETWORK_MODE,
        'networkCode': s.get('network_code') if NETWORK_MODE else None,
        'sttPasteKey': s.get('stt_paste_key'),
        'sttVoiceKey': s.get('stt_voice_key'),
        'sttJournalKey': s.get('stt_journal_key'),
        'sttBackend': s.get('stt_backend'),
        'ttsBackend': s.get('tts_backend'),
        'whisperModel': s.get('whisper_model'),
        'sttDevice': s.get('stt_device'),
        'voicePipelineEnabled': bool(s.get('voice_pipeline_enabled', 1)),
        'preventSleep': bool(s.get('prevent_sleep', 0)),
        'meetingEchoCancel': bool(s.get('meeting_echo_cancel', 0)),
        'nudgeEnabled': bool(s.get('nudge_enabled', 1)),
        'nudgeIntervalMinutes': s.get('nudge_interval_minutes') or 45,
        'briefingEnabled': bool(s.get('briefing_enabled', 1)),
        'briefingHour': s.get('briefing_hour') if s.get('briefing_hour') is not None else 5,
        'briefingModel': s.get('briefing_model'),
        'briefingGoals': s.get('briefing_goals') or '',
        'briefingReasoningEffort': s.get('briefing_reasoning_effort') or 'none',
        'briefingMaxTokens': s.get('briefing_max_tokens') or 16384,
        'briefingNumCtx': s.get('briefing_num_ctx') or 8192,
    })


@bp.patch('/ai')
def update_ai():
    body = request.json or {}
    field_map = {
        'ollamaUrl': 'ollama_url', 'ollamaModel': 'ollama_model',
        'llmReasoningEffort': 'llm_reasoning_effort',
        'llmMaxTokens': 'llm_max_tokens',
        'llmNumCtx': 'llm_num_ctx',
        'sttPasteKey': 'stt_paste_key', 'sttVoiceKey': 'stt_voice_key', 'sttJournalKey': 'stt_journal_key',
        'sttBackend': 'stt_backend', 'ttsBackend': 'tts_backend',
        'whisperModel': 'whisper_model', 'sttDevice': 'stt_device',
        'hfToken': 'hf_token',
        'voicePipelineEnabled': 'voice_pipeline_enabled',
        'preventSleep': 'prevent_sleep',
        'meetingEchoCancel': 'meeting_echo_cancel',
        'nudgeEnabled': 'nudge_enabled',
        'nudgeIntervalMinutes': 'nudge_interval_minutes',
        'briefingEnabled': 'briefing_enabled',
        'briefingHour': 'briefing_hour',
        'briefingModel': 'briefing_model',
        'briefingGoals': 'briefing_goals',
        'briefingReasoningEffort': 'briefing_reasoning_effort',
        'briefingMaxTokens': 'briefing_max_tokens',
        'briefingNumCtx': 'briefing_num_ctx',
    }
    updates: dict = {'updated_at': int(time.time())}
    for camel, snake in field_map.items():
        if camel in body:
            value = body[camel]
            if camel in ('briefingReasoningEffort', 'llmReasoningEffort'):
                # Reject anything outside Ollama's accepted levels rather than
                # storing a value the endpoint would 400 on later.
                if value not in REASONING_EFFORTS:
                    continue
            elif camel in ('briefingMaxTokens', 'llmMaxTokens'):
                # Clamp to a sane range; guards against a fat-fingered 0 that
                # would truncate every reply, or an absurd value.
                try:
                    value = max(256, min(65536, int(value)))
                except (TypeError, ValueError):
                    continue
            elif camel in ('briefingNumCtx', 'llmNumCtx'):
                # Context window; clamp to a range Ollama will accept without
                # blowing up VRAM. 512 floor keeps short prompts working.
                try:
                    value = max(512, min(131072, int(value)))
                except (TypeError, ValueError):
                    continue
            updates[snake] = value
    db = get_db()
    s = _get_settings()
    now = int(time.time())
    if s:
        build_update(db, 'settings', updates, 'id=1')
    else:
        updates['created_at'] = now
        updates['id'] = 1
        cols = ', '.join(updates)
        ph = ', '.join('?' * len(updates))
        db.execute(f'INSERT INTO settings({cols}) VALUES ({ph})', list(updates.values()))
    db.commit()
    if 'preventSleep' in body:
        _set_sleep_inhibitor(bool(body['preventSleep']))
    return jsonify({'success': True})


@bp.post('/regenerate-code')
def regenerate_code():
    code = str(random.randint(100000, 999999))
    db = get_db()
    db.execute('UPDATE settings SET network_code=?, updated_at=? WHERE id=1', (code, int(time.time())))
    db.commit()
    return jsonify({'networkCode': code})


@bp.get('/ollama-models')
def ollama_models():
    s = _get_settings()
    ollama_url = (s.get('ollama_url') if s else None) or 'http://localhost:11434'
    try:
        req = urllib.request.Request(f'{ollama_url}/api/tags', headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read())
        models = [
            # size is the on-disk file size; multiply by 1.2 to account for KV cache
            # and runtime overhead (typical real usage runs 10-30% above weights alone)
            {'name': m['name'], 'vramMb': round(m.get('size', 0) * 1.2 / (1024 * 1024))}
            for m in data.get('models', [])
        ]
        return jsonify(models)
    except Exception:
        return jsonify([])


_gpu_base_vram_mb: int | None = None
_gpu_total_vram_mb: int | None = None


def measure_base_gpu_vram() -> None:
    """Capture whatever's already using GPU VRAM (browser, compositor, other
    apps) once at process startup, before Lunaschal's own models are loaded —
    that's the "base" cost the VRAM budget in Settings needs to subtract from
    the card's total. Best-effort; leaves both values unset if nvidia-smi
    isn't available (e.g. no NVIDIA GPU). Only measures once per process —
    safe to call repeatedly (e.g. once per test's create_app())."""
    global _gpu_base_vram_mb, _gpu_total_vram_mb
    if _gpu_base_vram_mb is not None:
        return
    try:
        out = subprocess.run(
            ['nvidia-smi', '--query-gpu=memory.used,memory.total', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.strip()
        used_mb, total_mb = (int(x.strip()) for x in out.split(','))
        _gpu_base_vram_mb = used_mb
        _gpu_total_vram_mb = total_mb
    except Exception:
        _gpu_base_vram_mb = None
        _gpu_total_vram_mb = None


@bp.get('/gpu-vram')
def gpu_vram():
    if _gpu_base_vram_mb is None:
        return jsonify({'available': False})
    return jsonify({'available': True, 'baseMb': _gpu_base_vram_mb, 'totalMb': _gpu_total_vram_mb})
