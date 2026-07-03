import atexit
import logging
import os
import subprocess
from datetime import datetime, timezone

from flask import Flask, jsonify, request, send_from_directory

_listener_log = logging.getLogger('stt.listener')

# Suppress Werkzeug access-log spam from high-frequency polling endpoints
_SILENT_PATHS = {'/api/stt/listener-state', '/api/stt/health'}

class _SilentPollingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(p in msg for p in _SILENT_PATHS)

logging.getLogger('werkzeug').addFilter(_SilentPollingFilter())


def _start_listener():
    if not os.environ.get('STT_LISTENER'):
        return
    # Werkzeug debug reloader forks two processes; only start in the child (the real app).
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and os.environ.get('FLASK_DEBUG'):
        return
    stt_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'stt'))
    python  = os.path.join(stt_dir, '.venv', 'bin', 'python')
    script  = os.path.join(stt_dir, 'listener.py')
    if not os.path.exists(python) or not os.path.exists(script):
        _listener_log.warning("STT_LISTENER=1 but stt/.venv not found — run: bash stt/setup.sh")
        return
    proc = subprocess.Popen([python, script])
    _listener_log.info("Voice listener started (pid=%d)", proc.pid)
    atexit.register(proc.terminate)

DIST_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'dist'))

_EXEMPT_PATHS = {'/api/health', '/api/auth/status', '/api/auth/login', '/api/auth/logout'}


def create_app():
    app = Flask(__name__)

    from backend.db.connection import init_db
    init_db()

    from backend.auth import NETWORK_MODE, COOKIE_NAME, is_localhost, decode_token
    from backend.routes import journal, calendar, flashcard, settings, rag, chat, files, writing, stt, tasks, curated_tags
    from backend.routes import auth as auth_routes
    for bp in (auth_routes.bp, journal.bp, calendar.bp, flashcard.bp, settings.bp, rag.bp, chat.bp, files.bp, writing.bp, stt.bp, tasks.bp, curated_tags.bp):
        app.register_blueprint(bp)

    @app.before_request
    def check_auth():
        if not NETWORK_MODE or is_localhost(request):
            return None
        if request.path in _EXEMPT_PATHS or not request.path.startswith('/api/'):
            return None
        server_password = os.environ.get('LUNASCHAL_PASSWORD', '')
        if server_password and request.headers.get('X-Lunaschal-Password') == server_password:
            return None
        token = request.cookies.get(COOKIE_NAME)
        if not token or not decode_token(token):
            return jsonify({'error': 'Unauthorized', 'auth_required': True}), 401

    @app.get('/api/health')
    def health():
        return jsonify({'status': 'ok', 'timestamp': datetime.now(timezone.utc).isoformat()})

    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def serve_static(path):
        full = os.path.join(DIST_DIR, path)
        if path and os.path.isfile(full):
            return send_from_directory(DIST_DIR, path)
        return send_from_directory(DIST_DIR, 'index.html')

    _start_listener()
    return app
