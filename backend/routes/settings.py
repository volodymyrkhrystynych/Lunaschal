import json
import random
import time
import urllib.request
from flask import Blueprint, jsonify, request
from backend.auth import NETWORK_MODE
from backend.db.connection import get_db

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
        'aiProvider': s.get('ai_provider'),
        'aiModel': s.get('ai_model'),
        'hasOpenaiKey': bool(s.get('openai_api_key')),
        'hasGoogleKey': bool(s.get('google_api_key')),
        'ollamaUrl': s.get('ollama_url'),
        'ollamaModel': s.get('ollama_model'),
        'networkMode': NETWORK_MODE,
        'networkCode': s.get('network_code') if NETWORK_MODE else None,
        'sttPasteKey': s.get('stt_paste_key'),
        'sttVoiceKey': s.get('stt_voice_key'),
        'sttBackend': s.get('stt_backend'),
        'ttsBackend': s.get('tts_backend'),
        'whisperModel': s.get('whisper_model'),
        'voicePipelineEnabled': bool(s.get('voice_pipeline_enabled', 1)),
    })


@bp.patch('/ai')
def update_ai():
    body = request.json or {}
    field_map = {
        'aiProvider': 'ai_provider', 'aiModel': 'ai_model',
        'openaiApiKey': 'openai_api_key', 'googleApiKey': 'google_api_key',
        'ollamaUrl': 'ollama_url', 'ollamaModel': 'ollama_model',
        'sttPasteKey': 'stt_paste_key', 'sttVoiceKey': 'stt_voice_key',
        'sttBackend': 'stt_backend', 'ttsBackend': 'tts_backend',
        'whisperModel': 'whisper_model',
        'voicePipelineEnabled': 'voice_pipeline_enabled',
    }
    updates: dict = {'updated_at': int(time.time())}
    for camel, snake in field_map.items():
        if camel in body:
            updates[snake] = body[camel]
    db = get_db()
    s = _get_settings()
    now = int(time.time())
    if s:
        set_clause = ', '.join(f'{k}=?' for k in updates)
        db.execute(f'UPDATE settings SET {set_clause} WHERE id=1', list(updates.values()))
    else:
        updates['created_at'] = now
        updates['id'] = 1
        cols = ', '.join(updates)
        ph = ', '.join('?' * len(updates))
        db.execute(f'INSERT INTO settings({cols}) VALUES ({ph})', list(updates.values()))
    db.commit()
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
