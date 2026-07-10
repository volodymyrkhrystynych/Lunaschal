import time
from datetime import date

from ulid import ULID

from backend.db.connection import get_db
from backend.newspapers import scraper, storage


def sync_today() -> list[dict]:
    db = get_db()
    results = []
    for paper, info in storage.PAPERS.items():
        try:
            image_url = scraper.fetch_image_url(info['url'])
            # The edition's own date, not our clock's — frontpages.com can
            # still be serving yesterday's cover for a while after our local
            # midnight, and stamping it with today's date would silently
            # mislabel it (see scraper.py docstring).
            edition_date = scraper.extract_date(image_url) or date.today().isoformat()
            existing = db.execute(
                'SELECT id FROM newspaper_frontpages WHERE paper = ? AND date = ?',
                (paper, edition_date),
            ).fetchone()
            if existing:
                results.append({'paper': paper, 'status': 'already-saved', 'date': edition_date})
                continue
            data, content_type = scraper.download_image(image_url)
            path = storage.build_path(paper, edition_date, content_type)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            db.execute(
                'INSERT INTO newspaper_frontpages(id, paper, date, image_path, source_url, created_at)'
                ' VALUES (?,?,?,?,?,?)',
                (str(ULID()), paper, edition_date, str(path), image_url, int(time.time())),
            )
            db.commit()
            results.append({'paper': paper, 'status': 'downloaded', 'date': edition_date})
        except Exception as e:
            results.append({'paper': paper, 'status': 'error', 'error': str(e)})
    return results
