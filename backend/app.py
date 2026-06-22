import os
from datetime import datetime, timezone
from flask import Flask, jsonify, send_from_directory

DIST_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'dist'))


def create_app():
    app = Flask(__name__)

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
