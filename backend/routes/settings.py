import json
import os
import random
import signal
import subprocess
import time
import urllib.request
from flask import Blueprint, jsonify, request
from backend.auth import NETWORK_MODE
from backend.db.connection import build_update, get_db
from backend.geo import parse_coord

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
        'llamaUrl': s.get('llama_url'),
        'llamaModel': s.get('llama_model'),
        # Empty means journal photo captioning stays off — see backend/ai/images.py.
        'llamaVisionModel': s.get('llama_vision_model') or '',
        # Empty means audio description stays off — see backend/ai/audio_description.py.
        'llamaAudioModel': s.get('llama_audio_model') or '',
        # Whether the chat model itself reads photos attached to a message.
        # Needs an mmproj on the chat preset — see backend/ai/provider.py.
        'llamaChatVision': bool(s.get('llama_chat_vision', 0)),
        'llmThinking': bool(s.get('llm_thinking', 0)),
        'llmMaxTokens': s.get('llm_max_tokens') or 4096,
        # No llmNumCtx: the context window is fixed when llama-server loads the
        # model (ctx-size in llama/presets.ini), not settable per request.
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
        'transcribePolishEnabled': bool(s.get('transcribe_polish_enabled', 1)),
        'preventSleep': bool(s.get('prevent_sleep', 0)),
        'meetingEchoCancel': bool(s.get('meeting_echo_cancel', 0)),
        'nudgeEnabled': bool(s.get('nudge_enabled', 1)),
        'nudgeIntervalMinutes': s.get('nudge_interval_minutes') or 45,
        'briefingEnabled': bool(s.get('briefing_enabled', 1)),
        'briefingHour': s.get('briefing_hour') if s.get('briefing_hour') is not None else 5,
        'briefingModel': s.get('briefing_model'),
        'briefingGoals': s.get('briefing_goals') or '',
        'briefingThinking': bool(s.get('briefing_thinking', 0)),
        'briefingMaxTokens': s.get('briefing_max_tokens') or 16384,
        'websearchSearchProvider': s.get('websearch_search_provider') or '',
        # Never echo the key itself back to the client.
        'hasWebsearchSearchKey': bool(s.get('websearch_search_key')),
        'websearchSearxngUrl': s.get('websearch_searxng_url') or '',
        'repoContextEnabled': bool(s.get('repo_context_enabled', 1)),
        'repoContextHour': (
            s.get('repo_context_hour') if s.get('repo_context_hour') is not None else 3
        ),
        'researchEnabled': bool(s.get('research_enabled', 0)),
        'researchSearchProvider': s.get('research_search_provider') or '',
        # Never the key itself, following hasHfToken.
        'hasResearchSearchKey': bool(s.get('research_search_key')),
        'researchSearxngUrl': s.get('research_searxng_url') or '',
        # Wall-clock budgets. Defaults live in backend/delegate/limits.py and
        # are repeated here the way briefingMaxTokens' is — the route answers
        # for a settings row that predates the columns.
        'chatTimeoutEnabled': bool(s.get('chat_timeout_enabled', 1)),
        'chatTimeoutSeconds': s.get('chat_timeout_seconds') or 900,
        'researchTimeoutEnabled': bool(s.get('research_timeout_enabled', 1)),
        'researchSearchTimeoutSeconds': s.get('research_search_timeout_seconds') or 120,
        'researchDeepTimeoutSeconds': s.get('research_deep_timeout_seconds') or 600,
        'hasGoogleOauthClient': bool(s.get('google_oauth_client_id')) and bool(s.get('google_oauth_client_secret')),
        'hasMicrosoftOauthClient': bool(s.get('microsoft_oauth_client_id')) and bool(s.get('microsoft_oauth_client_secret')),
        'emailSyncEnabled': bool(s.get('email_sync_enabled', 1)),
        'emailSyncIntervalMinutes': s.get('email_sync_interval_minutes') or 15,
        'weatherDefaultLat': s.get('weather_default_lat'),
        'weatherDefaultLon': s.get('weather_default_lon'),
        'weatherDefaultLabel': s.get('weather_default_label') or '',
        # Tailored-resume retention. Defaults repeated from
        # backend/jobs/retention.py for a settings row predating the columns.
        'jobTriageEnabled': bool(s.get('job_triage_enabled', 1)),
        'jobRetentionDays': s.get('job_retention_days') or 180,
        'jobPurgeOnRejection': bool(s.get('job_purge_on_rejection', 1)),
        'jobRejectionGraceDays': (
            s.get('job_rejection_grace_days')
            if s.get('job_rejection_grace_days') is not None else 30
        ),
        # The key itself never leaves the server, same as the Google secret
        # above — the UI only needs to know whether Adzuna is configured.
        'hasAdzunaCredentials': bool(s.get('adzuna_app_id')) and bool(s.get('adzuna_app_key')),
    })


@bp.patch('/ai')
def update_ai():
    body = request.json or {}
    field_map = {
        'llamaUrl': 'llama_url', 'llamaModel': 'llama_model',
        'llamaVisionModel': 'llama_vision_model',
        'llamaAudioModel': 'llama_audio_model',
        'llamaChatVision': 'llama_chat_vision',
        'llmThinking': 'llm_thinking',
        'llmMaxTokens': 'llm_max_tokens',
        'sttPasteKey': 'stt_paste_key', 'sttVoiceKey': 'stt_voice_key', 'sttJournalKey': 'stt_journal_key',
        'sttBackend': 'stt_backend', 'ttsBackend': 'tts_backend',
        'whisperModel': 'whisper_model', 'sttDevice': 'stt_device',
        'hfToken': 'hf_token',
        'voicePipelineEnabled': 'voice_pipeline_enabled',
        'transcribePolishEnabled': 'transcribe_polish_enabled',
        'preventSleep': 'prevent_sleep',
        'meetingEchoCancel': 'meeting_echo_cancel',
        'nudgeEnabled': 'nudge_enabled',
        'nudgeIntervalMinutes': 'nudge_interval_minutes',
        'briefingEnabled': 'briefing_enabled',
        'briefingHour': 'briefing_hour',
        'briefingModel': 'briefing_model',
        'briefingGoals': 'briefing_goals',
        'briefingThinking': 'briefing_thinking',
        'briefingMaxTokens': 'briefing_max_tokens',
        'repoContextEnabled': 'repo_context_enabled',
        'repoContextHour': 'repo_context_hour',
        'researchEnabled': 'research_enabled',
        'researchSearchProvider': 'research_search_provider',
        'researchSearchKey': 'research_search_key',
        'researchSearxngUrl': 'research_searxng_url',
        'chatTimeoutEnabled': 'chat_timeout_enabled',
        'chatTimeoutSeconds': 'chat_timeout_seconds',
        'researchTimeoutEnabled': 'research_timeout_enabled',
        'researchSearchTimeoutSeconds': 'research_search_timeout_seconds',
        'researchDeepTimeoutSeconds': 'research_deep_timeout_seconds',
        # No context-window field at all: llama-server allocates the KV cache when
        # it loads the model, so the window is a serving-config concern
        # (llama/presets.ini), not a per-request one.
        'websearchSearchProvider': 'websearch_search_provider',
        'websearchSearchKey': 'websearch_search_key',
        'websearchSearxngUrl': 'websearch_searxng_url',
        'googleOauthClientId': 'google_oauth_client_id',
        'googleOauthClientSecret': 'google_oauth_client_secret',
        'microsoftOauthClientId': 'microsoft_oauth_client_id',
        'microsoftOauthClientSecret': 'microsoft_oauth_client_secret',
        'emailSyncEnabled': 'email_sync_enabled',
        'emailSyncIntervalMinutes': 'email_sync_interval_minutes',
        'weatherDefaultLat': 'weather_default_lat',
        'weatherDefaultLon': 'weather_default_lon',
        'weatherDefaultLabel': 'weather_default_label',
        'jobTriageEnabled': 'job_triage_enabled',
        'jobRetentionDays': 'job_retention_days',
        'jobPurgeOnRejection': 'job_purge_on_rejection',
        'jobRejectionGraceDays': 'job_rejection_grace_days',
        'adzunaAppId': 'adzuna_app_id',
        'adzunaAppKey': 'adzuna_app_key',
    }
    updates: dict = {'updated_at': int(time.time())}
    for camel, snake in field_map.items():
        if camel in body:
            value = body[camel]
            if camel in ('briefingThinking', 'llmThinking', 'repoContextEnabled',
                         'researchEnabled', 'llamaChatVision', 'chatTimeoutEnabled',
                         'researchTimeoutEnabled'):
                # Stored as 0/1 — Gemma 4's thinking channel is on or off, with no
                # graded levels to validate against.
                value = 1 if value else 0
            elif camel in ('briefingMaxTokens', 'llmMaxTokens'):
                # Clamp to a sane range; guards against a fat-fingered 0 that
                # would truncate every reply, or an absurd value.
                try:
                    value = max(256, min(65536, int(value)))
                except (TypeError, ValueError):
                    continue
            elif camel in ('chatTimeoutSeconds', 'researchSearchTimeoutSeconds',
                           'researchDeepTimeoutSeconds'):
                # Floor of 30s: anything less times out before a local model
                # has finished loading the prompt, which would read as the
                # feature being broken. Ceiling of two hours — past that the
                # timeout is not doing anything the enable flag doesn't.
                try:
                    value = max(30, min(7200, int(value)))
                except (TypeError, ValueError):
                    continue
            elif camel in ('weatherDefaultLat', 'weatherDefaultLon'):
                value = parse_coord(value)
                if value is None:
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


@bp.get('/llama-models')
def llama_models():
    """The router's known presets (llama/presets.ini) and whether each is loaded.

    Deliberately no VRAM estimate per model: with expert tensors split between
    GPU and system RAM, a model's GPU footprint is set by `n-cpu-moe`, the KV
    cache and the batch size — not by its file size. The old
    file-size-times-1.2 heuristic would be off by more than 10 GB here, so the
    VRAM panel reads the GPU live instead (see `/gpu-vram`).
    """
    s = _get_settings()
    llama_url = (s.get('llama_url') if s else None) or 'http://localhost:8080'
    try:
        req = urllib.request.Request(f'{llama_url}/models',
                                     headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read())
        models = []
        for m in data.get('data', []):
            status = m.get('status') or {}
            arch = m.get('architecture') or {}
            models.append({
                'name': m.get('id'),
                'status': status.get('value') or 'unknown',
                'inputModalities': arch.get('input_modalities') or ['text'],
                # Present only once a model is loaded and its child process has
                # reported back; None otherwise.
                'contextLength': m.get('n_ctx'),
            })
        return jsonify(models)
    except Exception:
        return jsonify([])


_gpu_base_vram_mb: int | None = None
_gpu_total_vram_mb: int | None = None


def _llm_server_vram_mb() -> int:
    """VRAM currently held by inference servers, read live.

    Needed because llama-server holds its allocation for as long as it runs and
    is started independently of Flask (see llama/lunaschal-llama.service), so it
    may well already be resident when this process starts. Without singling it
    out it would be counted as somebody else's "base" usage, and the budget panel
    would blame the browser for 6 GB of model weights.

    Matches Ollama's runner too, which is also called llama-server — during the
    migration both may be alive, and either way it's the LLM's cost.
    """
    try:
        out = subprocess.run(
            ['nvidia-smi', '--query-compute-apps=process_name,used_memory',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout
    except Exception:
        return 0
    total = 0
    for line in out.splitlines():
        name, _, mem = line.partition(',')
        if 'llama-server' in name:
            try:
                total += int(mem.strip())
            except ValueError:
                pass
    return total


def _gpu_used_total_mb() -> tuple[int, int] | None:
    try:
        out = subprocess.run(
            ['nvidia-smi', '--query-gpu=memory.used,memory.total', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.strip()
        used_mb, total_mb = (int(x.strip()) for x in out.split(','))
        return used_mb, total_mb
    except Exception:
        return None


def measure_base_gpu_vram() -> None:
    """Capture whatever's already using GPU VRAM (browser, compositor, other
    apps) once at process startup, before Lunaschal's own models are loaded —
    that's the "base" cost the VRAM budget in Settings needs to subtract from
    the card's total. Any inference server already running is excluded, since
    that's the LLM's cost and is reported separately and live. Best-effort;
    leaves both values unset if nvidia-smi isn't available (e.g. no NVIDIA GPU).
    Only measures once per process — safe to call repeatedly (e.g. once per
    test's create_app())."""
    global _gpu_base_vram_mb, _gpu_total_vram_mb
    if _gpu_base_vram_mb is not None:
        return
    reading = _gpu_used_total_mb()
    if reading is None:
        _gpu_base_vram_mb = None
        _gpu_total_vram_mb = None
        return
    used_mb, total_mb = reading
    _gpu_base_vram_mb = max(0, used_mb - _llm_server_vram_mb())
    _gpu_total_vram_mb = total_mb


@bp.get('/gpu-vram')
def gpu_vram():
    if _gpu_base_vram_mb is None:
        return jsonify({'available': False})
    reading = _gpu_used_total_mb()
    return jsonify({
        'available': True,
        'baseMb': _gpu_base_vram_mb,
        'totalMb': _gpu_total_vram_mb,
        # Live, unlike baseMb: the model's GPU share depends on n-cpu-moe, the KV
        # cache and batch size, so it's measured rather than derived from the
        # file size the way the Ollama-era panel did it.
        'usedMb': reading[0] if reading else None,
        'llmMb': _llm_server_vram_mb(),
    })
