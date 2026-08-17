import atexit
import logging
import os
import subprocess
from datetime import datetime, timezone

from flask import Flask, jsonify, request, send_from_directory

_listener_log = logging.getLogger('stt.listener')

# Suppress Werkzeug access-log spam from high-frequency polling endpoints
_SILENT_PATHS = {'/api/stt/listener-state', '/api/stt/health', '/api/meetings/active'}

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
    env = os.environ.copy()
    # Flask serves HTTPS-only in network mode (start-server.sh wires in a
    # Tailscale cert so iOS Safari can access the mic) — without this the
    # listener's defaults (http://127.0.0.1:5000 for both STT_URL, used by
    # /api/transcribe + /api/tts, and LUNASCHAL_URL, used by everything else)
    # get a connection reset and every voice shortcut silently stops working.
    tailscale_hostname = env.get('TAILSCALE_HOSTNAME')
    if tailscale_hostname:
        https_url = f'https://{tailscale_hostname}:5000'
        env.setdefault('STT_URL', https_url)
        env.setdefault('LUNASCHAL_URL', https_url)
    proc = subprocess.Popen([python, script], env=env)
    _listener_log.info("Voice listener started (pid=%d)", proc.pid)
    atexit.register(proc.terminate)

DIST_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'dist'))

_EXEMPT_PATHS = {'/api/health', '/api/auth/status', '/api/auth/login', '/api/auth/logout'}


def create_app():
    app = Flask(__name__)

    # Werkzeug 3.1 buffers non-file multipart fields in memory and rejects the
    # request with 413 once they exceed max_form_memory_size (500kB by default).
    # Handwritten Paper pages post a large strokes payload; it now travels as a
    # file part (see backend/routes/paper.py), but this headroom keeps any other
    # oversized text field from failing the same way. MAX_CONTENT_LENGTH is left
    # unset on purpose — meeting audio and book uploads are legitimately large.
    app.config['MAX_FORM_MEMORY_SIZE'] = 16 * 1024 * 1024

    from backend.db.connection import init_db
    init_db()

    from backend.auth import NETWORK_MODE, COOKIE_NAME, is_localhost, decode_token
    from backend.routes import journal, calendar, learning, settings, chat, files, writing, stt, tasks, curated_tags, shortcuts, transcriptions, cookbook, food, fanfic, newspapers, meetings, notebook, paper, lifestyle, ideas, practice, memory, email, notes, weather
    from backend.routes.settings import _get_settings, _set_sleep_inhibitor, measure_base_gpu_vram
    s = _get_settings()
    if s and s.get('prevent_sleep'):
        _set_sleep_inhibitor(True)
    # Snapshot GPU VRAM already in use (browser, compositor, etc.) before
    # Lunaschal's own models load, so the Settings VRAM budget can account
    # for it instead of assuming the whole card is free.
    measure_base_gpu_vram()
    from backend.routes import auth as auth_routes
    for bp in (auth_routes.bp, journal.bp, calendar.bp, learning.bp, settings.bp, chat.bp, files.bp, writing.bp, stt.bp, tasks.bp, curated_tags.bp, shortcuts.bp, transcriptions.bp, cookbook.bp, food.bp, fanfic.bp, newspapers.bp, meetings.bp, notebook.files_bp, notebook.bp, paper.bp, lifestyle.bp, ideas.bp, practice.bp, memory.bp, email.bp, notes.bp, weather.bp):
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
    # The sweeps are daemon loops with no stop signal, so every app that starts
    # them keeps its threads for the life of the process. A process that builds
    # many apps — the test suite builds one per test — piles up two threads per
    # app until thread creation fails and Python aborts, so the suite opts out.
    if not os.environ.get('LUNASCHAL_NO_SCHEDULERS'):
        from backend.chat_title_scheduler import start_title_scheduler
        start_title_scheduler()
        from backend.briefing_scheduler import start_briefing_scheduler
        start_briefing_scheduler()
        from backend.research.repo_scheduler import start_repo_context_scheduler
        start_repo_context_scheduler()
        from backend.research.research_scheduler import start_research_scheduler
        start_research_scheduler()
        from backend.email_scheduler import start_email_scheduler
        start_email_scheduler()
        # Lowest-priority loop in the app: nothing waits on an email image,
        # so it gets its own slow thread rather than a share of anything.
        from backend.email.images import start_image_fetcher
        start_image_fetcher()
    return app
