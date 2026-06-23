import os
from datetime import datetime, timezone
from flask import Flask, jsonify, request, send_from_directory

DIST_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'dist'))


def create_app():
    app = Flask(__name__)

    from backend.db.connection import init_db
    init_db()

    from backend.routes import journal, calendar, flashcard, settings, rag, chat, files
    for bp in (journal.bp, calendar.bp, flashcard.bp, settings.bp, rag.bp, chat.bp, files.bp):
        app.register_blueprint(bp)

    @app.get('/api/health')
    def health():
        return jsonify({'status': 'ok', 'timestamp': datetime.now(timezone.utc).isoformat()})

    @app.post('/api/transcribe')
    def transcribe():
        import requests as req
        stt_url = os.environ.get('STT_SERVICE_URL', 'http://127.0.0.1:8765')
        try:
            files = {k: (f.filename, f.stream, f.mimetype) for k, f in request.files.items()}
            resp = req.post(f'{stt_url}/transcribe', files=files, timeout=30)
            return jsonify(resp.json()), resp.status_code
        except req.exceptions.ConnectionError:
            return jsonify({'error': 'STT service not running. Start it with: ./stt/run_service.sh'}), 503
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.post('/api/tts')
    def tts():
        import requests as req
        stt_url = os.environ.get('STT_SERVICE_URL', 'http://127.0.0.1:8765')
        try:
            resp = req.post(f'{stt_url}/tts', data=request.form, timeout=30)
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
