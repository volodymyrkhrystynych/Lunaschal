import os
from datetime import datetime, timezone
from flask import Flask, jsonify, request, send_from_directory

DIST_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'dist'))

_EXEMPT_PATHS = {'/api/health', '/api/auth/status', '/api/auth/login', '/api/auth/logout'}


def create_app():
    app = Flask(__name__)

    from backend.db.connection import init_db
    init_db()

    from backend.auth import NETWORK_MODE, COOKIE_NAME, is_localhost, decode_token
    from backend.routes import journal, calendar, flashcard, settings, rag, chat, files, writing, stt
    from backend.routes import auth as auth_routes
    for bp in (auth_routes.bp, journal.bp, calendar.bp, flashcard.bp, settings.bp, rag.bp, chat.bp, files.bp, writing.bp, stt.bp):
        app.register_blueprint(bp)

    @app.before_request
    def check_auth():
        if not NETWORK_MODE or is_localhost(request):
            return None
        if request.path in _EXEMPT_PATHS or not request.path.startswith('/api/'):
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

    return app
