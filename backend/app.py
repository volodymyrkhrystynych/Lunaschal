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
    from backend.routes import journal, calendar, flashcard, settings, rag, chat, files, writing
    from backend.routes import auth as auth_routes
    for bp in (auth_routes.bp, journal.bp, calendar.bp, flashcard.bp, settings.bp, rag.bp, chat.bp, files.bp, writing.bp):
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

    @app.post('/api/transcribe')
    def transcribe():
        import requests as req
        stt_url = os.environ.get('STT_SERVICE_URL', 'http://127.0.0.1:8765')
        stt_token = os.environ.get('STT_AUTH_TOKEN')
        headers = {'Authorization': f'Bearer {stt_token}'} if stt_token else {}
        try:
            files = {k: (f.filename, f.stream, f.mimetype) for k, f in request.files.items()}
            resp = req.post(f'{stt_url}/transcribe', files=files, headers=headers, timeout=30)
            return jsonify(resp.json()), resp.status_code
        except req.exceptions.ConnectionError:
            return jsonify({'error': 'STT service not running. Start it with: ./stt/run_service.sh'}), 503
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.post('/api/tts')
    def tts():
        import requests as req
        stt_url = os.environ.get('STT_SERVICE_URL', 'http://127.0.0.1:8765')
        stt_token = os.environ.get('STT_AUTH_TOKEN')
        headers = {'Authorization': f'Bearer {stt_token}'} if stt_token else {}
        try:
            resp = req.post(f'{stt_url}/tts', data=request.form, headers=headers, timeout=30)
            return resp.content, resp.status_code, {'Content-Type': resp.headers.get('Content-Type', 'audio/wav')}
        except req.exceptions.ConnectionError:
            return jsonify({'error': 'STT service not running'}), 503
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def serve_static(path):
        full = os.path.join(DIST_DIR, path)
        if path and os.path.isfile(full):
            return send_from_directory(DIST_DIR, path)
        return send_from_directory(DIST_DIR, 'index.html')

    return app
