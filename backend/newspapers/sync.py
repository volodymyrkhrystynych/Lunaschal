import time
from datetime import date

from ulid import ULID

from backend.db.connection import get_db
from backend.newspapers import scraper, storage


def sync_today() -> list[dict]:
    today = date.today().isoformat()
    db = get_db()
    results = []
    for paper, info in storage.PAPERS.items():
        existing = db.execute(
            'SELECT id FROM newspaper_frontpages WHERE paper = ? AND date = ?',
            (paper, today),
        ).fetchone()
        if existing:
            results.append({'paper': paper, 'status': 'already-saved'})
            continue
        try:
            image_url = scraper.fetch_image_url(info['url'])
            data, content_type = scraper.download_image(image_url)
            path = storage.build_path(paper, today, content_type)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            db.execute(
                'INSERT INTO newspaper_frontpages(id, paper, date, image_path, source_url, created_at)'
                ' VALUES (?,?,?,?,?,?)',
                (str(ULID()), paper, today, str(path), image_url, int(time.time())),
            )
            db.commit()
            results.append({'paper': paper, 'status': 'downloaded'})
        except Exception as e:
            results.append({'paper': paper, 'status': 'error', 'error': str(e)})
    return results
