import os
import logging

import requests as _requests
from flask import Blueprint, Response, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint('stt', __name__)

STT_SERVICE_URL = os.environ.get('STT_SERVICE_URL', 'http://127.0.0.1:8765').rstrip('/')


def _service_request(method: str, path: str, **kwargs):
    url = f'{STT_SERVICE_URL}/{path}'
    try:
        resp = _requests.request(method, url, timeout=60, **kwargs)
        return Response(resp.content, status=resp.status_code,
                        content_type=resp.headers.get('Content-Type', 'application/json'))
    except _requests.exceptions.ConnectionError:
        return jsonify({'error': 'STT service unavailable — run stt/run_service.sh'}), 503
    except _requests.exceptions.Timeout:
        return jsonify({'error': 'STT service timed out'}), 504


@bp.get('/api/stt/health')
def stt_health():
    return _service_request('GET', 'health')


@bp.post('/api/transcribe')
def transcribe():
    return _service_request('POST', 'transcribe',
                             files={'audio': (request.files['audio'].filename,
                                              request.files['audio'].read(),
                                              request.files['audio'].content_type)},
                             data={'language': request.form.get('language', '')})


@bp.post('/api/tts')
def tts():
    return _service_request('POST', 'tts', data=request.form)
