"""Watch arbitrary company careers pages for newly-linked postings."""
import json
import re
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

from lxml import html as lxml_html
from ulid import ULID

from backend.db.connection import row_to_dict
from backend.jobs import ingest, profile as profile_mod, sync

JOB_HINT = re.compile(r'job|career|position|opening|vacanc|role', re.I)
MAX_LINKS = 60


def posting_links(html: str, base_url: str) -> list[str]:
    try:
        root = lxml_html.fromstring(html)
    except (ValueError, TypeError):
        return []
    base_host = urlsplit(base_url).hostname
    found = []
    for anchor in root.xpath('//a[@href]'):
        href = (anchor.get('href') or '').strip()
        text = ' '.join(anchor.text_content().split())
        if not href or not JOB_HINT.search(f'{href} {text}'):
            continue
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            continue
        # Strip tracking fragments; preserve query because some ATSes identify
        # a posting there.
        clean = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ''))
        if clean != base_url and clean not in found:
            found.append(clean)
        if len(found) >= MAX_LINKS:
            break
    return found


def create(db, url: str, label: str = '', interval_hours: int = 24) -> dict:
    html, final_url = ingest.fetch_html(url)
    links = posting_links(html, final_url)
    now = int(time.time())
    watch_id = str(ULID())
    db.execute(
        'INSERT INTO career_page_watches'
        ' (id, url, label, known_urls, interval_hours, last_run_at, last_count,'
        ' created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (watch_id, final_url, label.strip(), json.dumps(links),
         max(1, interval_hours), now, len(links), now, now),
    )
    db.commit()
    return get(db, watch_id)


def get(db, watch_id: str) -> dict | None:
    row = db.execute('SELECT * FROM career_page_watches WHERE id=?',
                     (watch_id,)).fetchone()
    return row_to_dict(row) if row else None


def run(db, watch_id: str, *, now: int | None = None) -> dict:
    row = db.execute('SELECT * FROM career_page_watches WHERE id=?',
                     (watch_id,)).fetchone()
    if row is None:
        raise LookupError(watch_id)
    now = int(time.time()) if now is None else now
    try:
        html, final_url = ingest.fetch_html(row['url'])
        links = posting_links(html, final_url)
        known = set(json.loads(row['known_urls'] or '[]'))
        new_links = [url for url in links if url not in known]
        loaded = profile_mod.load_profile(db)
        added = 0
        errors = []
        for url in new_links:
            try:
                job = ingest.ingest_url(url)
                job['sourceId'] = url
                if sync.upsert_job(db, 'manual', job, loaded):
                    added += 1
            except Exception as exc:
                errors.append(f'{url}: {exc}')
        db.execute(
            'UPDATE career_page_watches SET known_urls=?, last_run_at=?, last_count=?, '
            'last_error=?, updated_at=? WHERE id=?',
            (json.dumps(sorted(known | set(links))), now, len(links),
             '; '.join(errors)[:2000] or None, now, watch_id),
        )
        db.commit()
        return {'watchId': watch_id, 'found': len(links), 'new': len(new_links),
                'added': added, 'errors': errors}
    except Exception as exc:
        db.execute('UPDATE career_page_watches SET last_run_at=?, last_error=?, updated_at=? WHERE id=?',
                   (now, str(exc), now, watch_id))
        db.commit()
        return {'watchId': watch_id, 'found': 0, 'new': 0, 'added': 0,
                'errors': [str(exc)]}


def run_due(db, *, now: int | None = None) -> list[dict]:
    now = int(time.time()) if now is None else now
    rows = db.execute('SELECT * FROM career_page_watches WHERE enabled=1').fetchall()
    return [run(db, row['id'], now=now) for row in rows
            if row['last_run_at'] is None or now-row['last_run_at'] >= row['interval_hours']*3600]
