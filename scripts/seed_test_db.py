"""Build/refresh the test-environment seed database.

Reads `DATABASE_URL` and the feature `*_ROOT`/`SHORTCUTS_PATH` env vars exactly
like the app does (backend/db/connection.py, backend/storage.py) — don't pass
paths as flags, so this script and the app it seeds for can never disagree
about where the data lives. Meant to be invoked via test-env.sh, which exports
all of them pointed at a scratch directory; run directly it refuses to start
unless they're all set, so a bare `python scripts/seed_test_db.py` can never
wipe the real ./data/lunaschal.db.

    DATABASE_URL=data/test-run/lunaschal-test.db \
    FANFIC_ROOT=data/test-run/fanfic ... \
    .venv/bin/python scripts/seed_test_db.py

Idempotent by deletion, not by upsert: always wipes the target DB + scratch
media dirs and rebuilds from scratch. Realistic-but-small — a handful of rows
per feature, enough to click through every view, not exhaustive coverage
(that's what backend/tests/ is for).
"""
import json
import os
import shutil
import sys
import time
import wave
from pathlib import Path

REQUIRED_ENV_VARS = [
    'DATABASE_URL', 'FANFIC_ROOT', 'MEETINGS_ROOT', 'JOURNAL_ROOT',
    'JOURNAL_DRAFTS_ROOT', 'LIFESTYLE_ROOT', 'FOOD_ROOT', 'RECIPE_ROOT',
    'CHAT_ROOT', 'PAPER_ROOT', 'JOBS_ROOT', 'NEWSPAPERS_ROOT', 'NEWSPAPERS_ARCHIVE_ROOT',
    'NOTEBOOK_ROOT', 'EMAIL_MEDIA_ROOT', 'PIANO_ROOT', 'PIANO_ARCHIVE_ROOT',
    'FILES_ROOT', 'TORRENT_ROOT', 'STUDY_ROOT', 'STUDY_ARCHIVE_ROOT',
    'SHORTCUTS_PATH',
]


def _check_env() -> None:
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        sys.exit(
            'seed_test_db.py refuses to run with these unset: '
            f'{", ".join(missing)}\n'
            'Run it via ./test-env.sh, which exports all of them pointed at a '
            'scratch directory — running this script bare risks wiping the '
            'real ./data/lunaschal.db and production media.'
        )


_check_env()

# Repo root on sys.path so `backend.*` imports resolve when this is run
# directly (`python scripts/seed_test_db.py`), matching pytest.ini's
# `pythonpath = .` for the same reason.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ulid import ULID  # noqa: E402  (after the env-var guard, on purpose)
from PIL import Image, ImageDraw  # noqa: E402

from backend.day_boundary import day_key_for  # noqa: E402
from backend.db import connection  # noqa: E402
from backend.storage import IdScopedStorage  # noqa: E402
from backend.tags import tags_json  # noqa: E402

DAY = 86400


def new_id() -> str:
    return str(ULID())


def today_key(days_ago: int = 0) -> str:
    """The app's notion of "today", not the wall clock's.

    Days roll over at 4am (backend/day_boundary.py), and the routes that read a
    `date`/`day_key` column for today go through day_key_for(). Seeding
    time.strftime('%Y-%m-%d') instead looks identical all afternoon and then
    silently seeds tomorrow's key between midnight and 4am — the demo comes up
    with an empty calorie log and an empty todo bar for anyone who runs it late
    at night.
    """
    return day_key_for(int(time.time()) - days_ago * DAY)


def ts(days_ago: int = 0, hours_ago: int = 0) -> int:
    return int(time.time()) - days_ago * DAY - hours_ago * 3600


def placeholder_image(path: Path, label: str, size=(640, 400), color=(90, 110, 140)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new('RGB', size, color)
    draw = ImageDraw.Draw(img)
    draw.text((size[0] // 2, size[1] // 2), label, fill=(255, 255, 255), anchor='mm')
    img.save(path, 'JPEG', quality=80)


def placeholder_audio(path: Path, seconds: float = 0.5) -> None:
    """A real (silent) WAV, so anything that probes the file finds a valid one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b'\x00\x00' * int(16000 * seconds))


def placeholder_text(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')


def placeholder_pdf(path: Path, pages: list[str]) -> None:
    """A real multi-page PDF, because the Study tab renders it with pdf.js
    rather than handing it to the browser — a stub file would fail to parse and
    the viewer would show its error state instead of the feature."""
    path.parent.mkdir(parents=True, exist_ok=True)
    images = []
    for label in pages:
        img = Image.new('RGB', (1240, 1754), (250, 249, 246))  # A4 at 150dpi
        draw = ImageDraw.Draw(img)
        draw.text((620, 877), label, fill=(40, 40, 40), anchor='mm')
        images.append(img)
    images[0].save(path, 'PDF', save_all=True, append_images=images[1:])


# journal_voice_drafts is the one media feature whose storage module keeps its
# IdScopedStorage private (backend/journal/voice_drafts.py), so the same layout
# is rebuilt here from the same class rather than reaching for the underscore.
voice_draft_storage = IdScopedStorage('JOURNAL_DRAFTS_ROOT', './data/journal_drafts')


def wipe_scratch() -> None:
    db_path = Path(os.environ['DATABASE_URL'])
    for suffix in ('', '-wal', '-shm'):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    for var in REQUIRED_ENV_VARS:
        if var in ('DATABASE_URL', 'SHORTCUTS_PATH'):
            continue
        root = Path(os.environ[var])
        if root.exists():
            shutil.rmtree(root)
    shortcuts_path = Path(os.environ['SHORTCUTS_PATH'])
    if shortcuts_path.exists():
        shortcuts_path.unlink()


def seed_journal(db):
    from backend.journal.storage import attachment_dir, attachment_path

    entries = [
        (new_id(), 'Morning pages', 'Woke up early and got a good hour of writing in before the day got noisy. Feeling optimistic about the week.', ['journal', 'writing'], 6),
        (new_id(), 'Long walk', 'Took the long way home along the river. Cold enough to see my breath, which always makes a walk feel like an event rather than a chore.', ['journal', 'outside'], 3),
        (new_id(), '', 'Quick note: need to call the dentist back about rescheduling. Also finally fixed the squeaky drawer in the kitchen.', ['journal'], 1),
    ]
    for entry_id, title, content, tags, days_ago in entries:
        db.execute(
            'INSERT INTO journal_entries (id, content, raw_content, title, tags, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (entry_id, content, content, title, tags_json(tags), ts(days_ago), ts(days_ago)),
        )

    # One image attachment on the oldest entry.
    attachment_id = new_id()
    entry_id = entries[-1][0]
    d = attachment_dir(attachment_id)
    d.mkdir(parents=True, exist_ok=True)
    img_path = attachment_path(attachment_id, 'jpg')
    placeholder_image(img_path, 'kitchen drawer')
    db.execute(
        'INSERT INTO journal_attachments (id, entry_id, kind, name, path, mime, size, position, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (attachment_id, entry_id, 'image', 'fixed drawer', str(img_path), 'image/jpeg',
         img_path.stat().st_size, 0, ts(1)),
    )

    # Curated tags: user-defined in Settings → Tags, applied to entries by a
    # background classifier. Seed the tag plus one match so the Journal filter
    # pills have something to filter by without the scan ever running.
    tag_ids = []
    for name in ('gratitude', 'health'):
        tag_id = new_id()
        tag_ids.append(tag_id)
        db.execute(
            'INSERT INTO curated_tags (id, name, created_at) VALUES (?, ?, ?)',
            (tag_id, name, ts(25)),
        )
    db.execute(
        'INSERT INTO journal_entry_curated_tags (entry_id, tag_id) VALUES (?, ?)',
        (entries[0][0], tag_ids[0]),
    )
    db.execute(
        'INSERT INTO journal_entry_curated_tags (entry_id, tag_id) VALUES (?, ?)',
        (entries[-1][0], tag_ids[1]),
    )

    # Two voice drafts. 'processing' is deliberately avoided — the startup
    # sweep flips that to 'error' — and the 'done' one alone would leave the
    # drafts dropdown empty, since list_drafts() shows only status != 'done'
    # (a done draft is already a journal entry in the feed).
    drafts = [
        ('done', '["Took the long way home along the river."]', entries[1][0], None),
        ('error', None, None, 'Transcription failed: no speech detected.'),
    ]
    for status, candidates, entry_id_ref, error in drafts:
        draft_id = new_id()
        draft_path = voice_draft_storage.dir(draft_id) / 'draft.wav'
        placeholder_audio(draft_path)
        db.execute(
            'INSERT INTO journal_voice_drafts (id, path, mime, size, status, error, candidates, '
            'entry_id, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (draft_id, str(draft_path), 'audio/wav', draft_path.stat().st_size, status, error,
             candidates, entry_id_ref, ts(3), ts(3)),
        )
    return [e[0] for e in entries]


def seed_calendar(db, journal_ids):
    today = time.strftime('%Y-%m-%d')
    yesterday = time.strftime('%Y-%m-%d', time.localtime(time.time() - DAY))
    next_week = time.strftime('%Y-%m-%d', time.localtime(time.time() + 7 * DAY))

    past_id = new_id()
    db.execute(
        'INSERT INTO calendar_events (id, title, description, date, time, end_time, all_day, tags, journal_id, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)',
        (past_id, 'Dentist checkup', 'Routine cleaning', yesterday, '10:00', '10:30', tags_json(['health']), journal_ids[-1], ts(1)),
    )
    db.execute(
        'INSERT INTO calendar_journal_links (id, calendar_event_id, journal_entry_id, created_at) VALUES (?, ?, ?, ?)',
        (new_id(), past_id, journal_ids[-1], ts(1)),
    )
    db.execute(
        'INSERT INTO calendar_events (id, title, description, date, time, end_time, all_day, tags, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)',
        (new_id(), 'Team sync', 'Weekly check-in', today, '14:00', '14:30', tags_json(['work']), ts(0)),
    )
    db.execute(
        'INSERT INTO calendar_events (id, title, description, date, all_day, tags, created_at) '
        'VALUES (?, ?, ?, ?, 1, ?, ?)',
        (new_id(), "Friend's birthday", '', next_week, tags_json(['family']), ts(0)),
    )
    gym_id = new_id()
    db.execute(
        'INSERT INTO calendar_events '
        '(id, title, description, date, time, all_day, tags, created_at, repeat_freq, repeat_interval, repeat_byweekday) '
        'VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)',
        (gym_id, 'Gym', 'Leg day', today, '07:00', tags_json(['exercise']), ts(0), 'weekly', 1, '1,3,5'),
    )
    # Both exception kinds on the recurring event, so the "this occurrence
    # only" edit paths have something to render.
    skipped = time.strftime('%Y-%m-%d', time.localtime(time.time() + 7 * DAY))
    moved = time.strftime('%Y-%m-%d', time.localtime(time.time() + 14 * DAY))
    db.execute(
        'INSERT INTO calendar_event_exceptions (id, event_id, date, action, created_at) '
        'VALUES (?, ?, ?, ?, ?)',
        (new_id(), gym_id, skipped, 'skip', ts(0)),
    )
    db.execute(
        'INSERT INTO calendar_event_exceptions (id, event_id, date, action, new_date, new_time, new_end_time, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (new_id(), gym_id, moved, 'move', moved, '18:00', '19:00', ts(0)),
    )


def seed_learning(db):
    folder_id = new_id()
    db.execute(
        'INSERT INTO learning_folders (id, name, position, created_at, updated_at) VALUES (?, ?, 0, ?, ?)',
        (folder_id, 'Spanish vocab', ts(20), ts(20)),
    )
    cards = [
        ('What is "the bridge" in Spanish?', 'el puente', 'active', ts(2)),
        ('What is "to forget" in Spanish?', 'olvidar', 'active', ts(1)),
        ('What is "nevertheless" in Spanish?', 'sin embargo', 'pending', None),
    ]
    card_ids = []
    for question, answer, state, due in cards:
        card_id = new_id()
        card_ids.append(card_id)
        db.execute(
            'INSERT INTO learning_cards (id, folder_id, question, answer, state, due, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (card_id, folder_id, question, answer, state, due, ts(20), ts(2)),
        )
    db.execute(
        'INSERT INTO learning_reviews (id, card_id, rating, user_answer, created_at) VALUES (?, ?, ?, ?, ?)',
        (new_id(), card_ids[0], 3, 'el puente', ts(2)),
    )
    # One graded attempt (card_id is UNIQUE here — it's the in-progress answer
    # for a card, not a history) and one revision of a card the verification
    # pass corrected.
    db.execute(
        'INSERT INTO learning_attempts (id, card_id, mode, answer, answer_mode, grade_status, '
        'coverage, suggested_rating, normalized_answer, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (new_id(), card_ids[1], 'answered', 'olvidar', 'typed', 'done',
         '[{"claim": "olvidar", "covered": true}]', 3, 'olvidar', ts(1), ts(1)),
    )
    db.execute(
        'INSERT INTO learning_revisions (id, old_card_id, new_card_id, trigger_type, old_answer, '
        'new_answer, diff, is_semantic, sources, note, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (new_id(), card_ids[0], card_ids[0], 'manual_edit', 'el puento', 'el puente',
         '- el puento\n+ el puente', 0, None, 'Typo in the original answer.', ts(2)),
    )


def seed_cookbook(db):
    from backend.cookbook.storage import media_path, recipe_dir

    recipe_id = new_id()
    db.execute(
        'INSERT INTO recipes (id, title, content, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
        (recipe_id, 'Weeknight lentil soup',
         '## Ingredients\n- 1 cup red lentils\n- 1 onion\n- 2 carrots\n- 1 tsp cumin\n\n'
         '## Steps\n1. Saute onion and carrots.\n2. Add lentils, cumin, and 4 cups water.\n'
         '3. Simmer 25 minutes until lentils are soft.',
         tags_json(['soup', 'vegetarian', 'quick']), ts(10), ts(10)),
    )
    recipe_dir(recipe_id).mkdir(parents=True, exist_ok=True)
    media_id = new_id()
    img_path = media_path(recipe_id, media_id, 'jpg')
    placeholder_image(img_path, 'lentil soup')
    db.execute(
        'INSERT INTO recipe_media (id, recipe_id, kind, path, mime, position, created_at) '
        'VALUES (?, ?, ?, ?, ?, 0, ?)',
        (media_id, recipe_id, 'image', str(img_path), 'image/jpeg', ts(10)),
    )
    return recipe_id


def seed_food(db, recipe_id):
    from backend.food.storage import media_path

    entries = [
        (None, 'Lentil soup', 'Home', 5, ts(1)),
        (recipe_id, 'Lentil soup, leftovers', 'Home', 4, ts(0)),
        (None, 'Pad thai', 'Thai place downtown', 4, ts(3)),
    ]
    entry_ids = []
    for rid, dish, place, rating, when in entries:
        entry_id = new_id()
        entry_ids.append(entry_id)
        db.execute(
            'INSERT INTO food_entries (id, dish, place, rating, recipe_id, latitude, longitude, '
            'created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (entry_id, dish, place, rating, rid, 43.6532, -79.3832, when, when),
        )

    media_id = new_id()
    img_path = media_path(entry_ids[-1], media_id, 'jpg')
    placeholder_image(img_path, 'pad thai')
    db.execute(
        'INSERT INTO food_media (id, entry_id, kind, path, mime, position, created_at) '
        'VALUES (?, ?, ?, ?, ?, 0, ?)',
        (media_id, entry_ids[-1], 'image', str(img_path), 'image/jpeg', ts(3)),
    )

    # A meal that was talked over as well as photographed. 'done', never
    # 'running': an in-flight state is rewritten by init_db()'s orphan reset on
    # the next start, so seeding one shows a status that cannot last.
    clip_id = new_id()
    clip_path = media_path(entry_ids[-1], clip_id, 'wav')
    placeholder_audio(clip_path)
    db.execute(
        'INSERT INTO food_media (id, entry_id, kind, path, mime, position, '
        'transcript, transcript_status, created_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)',
        (clip_id, entry_ids[-1], 'audio', str(clip_path), 'audio/wav',
         'Ordered the pad thai again. Still the best on the street.',
         'done', ts(3)),
    )

    today = today_key()
    for description, calories in [('Oatmeal and coffee', 420), ('Lentil soup', 380), ('Pad thai', 700)]:
        db.execute(
            'INSERT INTO calorie_logs (id, date, description, calories, created_at) VALUES (?, ?, ?, ?, ?)',
            (new_id(), today, description, calories, ts(0)),
        )


def seed_fanfic(db, journal_ids):
    from backend.fanfic.storage import fic_dir

    fic_id = new_id()
    db.execute(
        'INSERT INTO fics (id, title, author, source_type, description, word_count, chapter_count, '
        'download_status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (fic_id, 'The Lighthouse Keeper of Ashworth Bay', 'a_quiet_author', 'epub',
         'A retired sea captain, Lyra Ashworth, discovers the lighthouse she keeps holds more than a light.',
         820, 2, 'complete', ts(15), ts(15)),
    )
    fic_dir(fic_id).mkdir(parents=True, exist_ok=True)
    chapters = [
        (1, 'Chapter 1: The Storm', 'Lyra Ashworth had weathered forty winters at the lighthouse, but never one like this.'),
        (2, 'Chapter 2: What the Light Found', 'By morning, Lyra Ashworth understood the lighthouse had been keeping its own watch all along.'),
    ]
    for position, title, text in chapters:
        db.execute(
            'INSERT INTO fic_chapters (id, fic_id, position, title, content_html, content_text, word_count, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (new_id(), fic_id, position, title, f'<p>{text}</p>', text, len(text.split()), ts(15)),
        )

    folder_id = new_id()
    db.execute(
        'INSERT INTO fic_folders (id, name, position, created_at, updated_at) VALUES (?, ?, 0, ?, ?)',
        (folder_id, 'Currently reading', ts(15), ts(15)),
    )
    db.execute(
        'INSERT INTO fic_folder_items (folder_id, fic_id, created_at) VALUES (?, ?, ?)',
        (folder_id, fic_id, ts(15)),
    )
    first_chapter = db.execute(
        'SELECT id FROM fic_chapters WHERE fic_id = ? ORDER BY position LIMIT 1', (fic_id,)
    ).fetchone()
    db.execute(
        'INSERT INTO fic_bookmarks (id, fic_id, chapter_id, type, scroll_position, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (new_id(), fic_id, first_chapter['id'], 'continue', 0.4, ts(1)),
    )
    db.execute(
        'INSERT INTO fic_chapter_reads (chapter_id, fic_id, created_at) VALUES (?, ?, ?)',
        (first_chapter['id'], fic_id, ts(1)),
    )
    for name in ('slow burn', 'found family', 'lighthouse'):
        db.execute(
            'INSERT INTO fic_site_tags (fic_id, name, created_at) VALUES (?, ?, ?)',
            (fic_id, name, ts(15)),
        )

    # Site bookkeeping. The cookie is obviously fake on purpose — this database
    # is built from a script that ships in a public repo.
    db.execute(
        'INSERT INTO site_cookies (domain, cookie, user_agent, updated_at) VALUES (?, ?, ?, ?)',
        ('forums.example.com', 'xf_session=fake-not-a-real-session-cookie',
         'Mozilla/5.0 (X11; Linux x86_64) Lunaschal/demo', ts(15)),
    )
    db.execute(
        'INSERT INTO fanfic_watched_scans (domain, next_page, found, imported, already_in_library, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        ('forums.example.com', 3, 12, 1, 11, ts(2)),
    )

    # Reading commentary — this is what interleaves a fic into the Journal feed.
    db.execute(
        'INSERT INTO journal_entry_fic_refs (id, journal_entry_id, fic_id, chapter_id, created_at) '
        'VALUES (?, ?, ?, ?, ?)',
        (new_id(), journal_ids[1], fic_id, first_chapter['id'], ts(3)),
    )


def seed_newspapers(db):
    from backend.newspapers.storage import build_path
    from backend.newspapers.issues import issue_path
    from pypdf import PdfWriter

    today = today_key()
    for paper in ('toronto-star', 'nyt'):
        path = build_path(paper, today, 'image/jpeg')
        placeholder_image(path, paper, size=(800, 1000))
        db.execute(
            'INSERT INTO newspaper_frontpages (id, paper, date, image_path, created_at) VALUES (?, ?, ?, ?, ?)',
            (new_id(), paper, today, str(path), ts(0)),
        )
    path = issue_path(today)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(str(path))
    db.execute(
        "INSERT INTO newspaper_downloads (id, date, status, created_at, updated_at) VALUES (?, ?, 'complete', ?, ?)",
        (new_id(), today, ts(0), ts(0)),
    )
    db.execute(
        'INSERT INTO newspaper_issues (id, date, pdf_path, byte_size, page_count, created_at) VALUES (?, ?, ?, ?, ?, ?)',
        (new_id(), today, str(path), path.stat().st_size, 1, ts(0)),
    )


def seed_torrents(db):
    """A couple of finished downloads.

    Only what qBittorrent has no concept of is stored here — the note and the
    retention policy — so a seeded row on its own renders as an *untracked*
    torrent until a client is running. That is correct rather than a gap: the
    tab is a view of the client, and there is no client in a demo environment.
    The files on disk are what make the view show something either way.

    Both rows are finished on purpose. There is no in-flight torrent state in
    this schema to seed wrongly, but a half-downloaded demo file would still be
    a lie about a download nothing is progressing.
    """
    from backend.torrent.storage import torrent_root

    rows = [
        ('c9e15763f722f23e98a29decdfae341b98d53056', 'debian-13.1.0-amd64-netinst.iso',
         'magnet', 'Keep — reinstall media', None, 9, 8),
        ('5a8e0f2b1c4d3e6f7a8b9c0d1e2f3a4b5c6d7e8f', 'Blender Open Movie - Sprite Fright',
         'magnet', 'Watched, safe to clear', 30, 40, 38),
    ]
    for info_hash, name, source, note, retention_days, added_days, done_days in rows:
        # A real file so the per-torrent file list and the streaming route have
        # something to serve in the demo.
        path = torrent_root() / name / f'{name}.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'lunaschal demo payload\n' * 64)
        db.execute(
            'INSERT INTO torrents (id, info_hash, name, source, magnet_uri, note,'
            ' retention_days, added_at, completed_at, created_at, updated_at)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (new_id(), info_hash, name, source, f'magnet:?xt=urn:btih:{info_hash}&dn={name}',
             note, retention_days, ts(added_days), ts(done_days), ts(added_days),
             ts(done_days)),
        )


def seed_jobs(db, email_ids):
    from backend.jobs.storage import fill_run_screenshot_path, resume_path

    # init_db()'s migrations already seed a default job_profile row (id=1,
    # same singleton idiom as `settings`) — fill it in rather than insert.
    db.execute(
        'UPDATE job_profile SET full_name = ?, email = ?, phone = ?, location = ?, links = ?, '
        'headline = ?, summary = ?, work_authorization = ?, salary_expectation = ?, '
        'notice_period = ?, availability_date = ?, relocation_willingness = ?, '
        'allowed_locations = ?, remote_only = ?, soft_salary_floor = ?, max_distance_km = ?, '
        'max_posting_age_days = ?, soft_preferences = ?, updated_at = ? WHERE id = 1',
        ('Jordan Rivera', 'jordan.rivera@example.com', '555-0100', 'Toronto, ON',
         '["https://example.com/jordan", "https://github.com/example"]',
         'Backend engineer', 'Backend engineer with 6 years building data-heavy web services.',
         'Canadian citizen', '$140,000 CAD', '2 weeks', 'Immediately', 'Not willing to relocate',
         'Toronto, ON\nRemote (Canada)', 0, 130000.0, 50.0, 60,
         'Prefers small teams and product work over pure platform.', ts(30)),
    )
    role_id = new_id()
    db.execute(
        'INSERT INTO profile_roles (id, company, title, location, start_label, end_label, ord, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)',
        (role_id, 'Acme Corp', 'Senior Backend Engineer', 'Toronto, ON', '2021', 'Present', ts(30), ts(30)),
    )
    for i, text in enumerate([
        'Led migration of the billing service from a monolith to independent workers, cutting p95 latency by 40%.',
        'Designed the on-call rotation and incident-review process adopted across three teams.',
    ]):
        db.execute(
            'INSERT INTO profile_bullets (id, role_id, text, ord, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
            (new_id(), role_id, text, i, ts(30), ts(30)),
        )
    for i, (name, category) in enumerate([('Python', 'languages'), ('PostgreSQL', 'databases'), ('Docker', 'infra')]):
        db.execute(
            'INSERT INTO profile_skills (id, name, category, ord, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
            (new_id(), name, category, i, ts(30), ts(30)),
        )

    for i, (institution, credential, field, start, end) in enumerate([
        ('University of Toronto', 'BSc', 'Computer Science', '2014', '2018'),
    ]):
        db.execute(
            'INSERT INTO profile_education (id, institution, credential, field, start_label, '
            'end_label, notes, ord, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (new_id(), institution, credential, field, start, end, '', i, ts(30), ts(30)),
        )

    # The Answer Kit: reusable answers to the questions every application asks.
    for i, (slug, question, answer) in enumerate([
        ('why-us', 'Why do you want to work here?',
         'The payments problem is the part of the stack I have spent the last three years in, and this team owns it end to end.'),
        ('greatest-strength', 'What is your greatest strength?',
         'Turning an on-call pager into a list of fixable causes — I did exactly that for the billing service.'),
        ('notice', 'When can you start?', 'Two weeks from an offer.'),
    ]):
        db.execute(
            'INSERT INTO profile_answers (id, slug, question, answer, tags, ord, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (new_id(), slug, question, answer, tags_json(['general']), i, ts(30), ts(30)),
        )

    job_id = new_id()
    db.execute(
        'INSERT INTO jobs (id, source, source_id, url, company, title, location, remote, '
        'salary_min, salary_max, salary_currency, description, match_score, match_reasons, '
        'triage_state, triage_fit, triage_summary, triage_at, work_location, distance_km, '
        'distance_precision, posted_at, fetched_at, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (job_id, 'manual', job_id, 'https://example.com/jobs/1', 'Globex', 'Backend Engineer',
         'Remote', 130000.0, 160000.0, 'CAD', 'Own the payments API and its on-call.',
         0.82, '["Python", "on-call ownership", "salary above floor"]',
         'kept', 'strong', 'Payments-focused backend role, remote, salary above the stated floor.',
         ts(5), 'remote', 0.0, 'exact', ts(6), ts(5), ts(5), ts(5)),
    )
    # A second posting still in the triage feed, so the phone's triage view has
    # something pending to swipe.
    pending_job_id = new_id()
    db.execute(
        'INSERT INTO jobs (id, source, source_id, url, company, title, location, remote, '
        'description, triage_state, posted_at, fetched_at, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)',
        (pending_job_id, 'greenhouse', 'gh-4821', 'https://example.com/jobs/2', 'Initech',
         'Platform Engineer', 'Toronto, ON', 'Kubernetes, Terraform, and an internal PaaS.',
         'pending', ts(2), ts(1), ts(1), ts(1)),
    )

    application_id = new_id()
    db.execute(
        'INSERT INTO applications (id, job_id, status, steer, cover_letter, cover_letter_required, '
        'notes, applied_email, applied_at, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)',
        (application_id, job_id, 'interview',
         'Emphasise the payments work and keep it to one page.',
         'I have spent three years on a payments API much like this one.',
         'Recruiter screen went well; technical round is next.',
         'jordan.rivera@example.com', ts(4), ts(5), ts(2)),
    )
    for status, source, days_ago in [
        ('submitted', 'manual', 4), ('acknowledged', 'email', 3), ('interview', 'email', 2),
    ]:
        db.execute(
            'INSERT INTO application_status_events (id, application_id, status, source, source_id, occurred_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (new_id(), application_id, status, source, None, ts(days_ago)),
        )

    # The tailored resume, with its rendered files on disk.
    version_id = new_id()
    pdf_path = resume_path(application_id, version_id, 'pdf')
    docx_path = resume_path(application_id, version_id, 'docx')
    placeholder_text(pdf_path, '%PDF-1.4\n% placeholder resume for the demo database\n')
    placeholder_text(docx_path, 'placeholder resume for the demo database\n')
    db.execute(
        'INSERT INTO resume_versions (id, application_id, label, content, keywords, review, html, '
        'pdf_path, docx_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (version_id, application_id, 'Globex — Backend Engineer',
         '{"summary": "Backend engineer, payments and billing.", '
         '"roles": [{"company": "Acme Corp", "bullets": ["Cut billing p95 latency by 40%."]}]}',
         '["payments", "Python", "on-call"]',
         'Kept every bullet traceable to a profile bullet; nothing invented.',
         '<h1>Jordan Rivera</h1><p>Backend engineer, payments and billing.</p>',
         str(pdf_path), str(docx_path), ts(4)),
    )

    for i, (question, answer, source) in enumerate([
        ('Why are you interested in this role?',
         'The payments problem is the part of the stack I have spent three years in.', 'generated'),
        ('Do you require sponsorship?', 'No — Canadian citizen.', 'profile'),
    ]):
        db.execute(
            'INSERT INTO application_answers (id, application_id, question, answer, source, '
            'page_url, ord, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (new_id(), application_id, question, answer, source,
             'https://example.com/jobs/1/apply', i, ts(4), ts(4)),
        )

    db.execute(
        'INSERT INTO application_research (id, application_id, content, created_at) VALUES (?, ?, ?, ?)',
        (new_id(), application_id,
         '## Globex\n\nPrivately held, ~400 people. The payments team was spun out of the '
         'billing group last year; public engineering blog covers their ledger rewrite.', ts(3)),
    )
    db.execute(
        'INSERT INTO interview_prep_packs (id, application_id, content, created_at) VALUES (?, ?, ?, ?)',
        (new_id(), application_id,
         '## Likely questions\n\n- Walk through the billing migration.\n'
         '- How do you decide what pages on-call?\n\n## Ask them\n\n- What broke most recently?',
         ts(2)),
    )

    # What the browser extension recorded when it filled the real form.
    run_id = new_id()
    screenshot_path = fill_run_screenshot_path(application_id, run_id)
    placeholder_image(screenshot_path, 'fill run', size=(900, 600))
    db.execute(
        'INSERT INTO application_fill_runs (id, application_id, page_url, page_title, fields, '
        'screenshot_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (run_id, application_id, 'https://example.com/jobs/1/apply', 'Apply — Backend Engineer',
         '[{"label": "Full name", "value": "Jordan Rivera", "filled": true}, '
         '{"label": "Resume", "value": "jordan-rivera-globex.pdf", "filled": true}]',
         str(screenshot_path), ts(4)),
    )

    # Email linkage: the confirmation and the scan bookkeeping that found it.
    db.execute(
        'INSERT INTO job_email_links (id, application_id, email_id, link_kind, confidence, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (new_id(), application_id, email_ids['confirmation'], 'auto', 0.94, ts(3)),
    )
    for email_id in email_ids.values():
        db.execute(
            'INSERT INTO job_email_scans (email_id, scanned_at, matched) VALUES (?, ?, ?)',
            (email_id, ts(1), 1 if email_id == email_ids['confirmation'] else 0),
        )

    # Discovery: the board adapters the sync sweep walks.
    db.execute(
        'INSERT INTO job_searches (id, kind, label, params, enabled, interval_hours, last_run_at, '
        'last_count, created_at, updated_at) VALUES (?, ?, ?, ?, 1, 24, ?, ?, ?, ?)',
        (new_id(), 'greenhouse', 'Initech — Greenhouse', '{"board": "initech"}', ts(1), 14, ts(20), ts(1)),
    )
    db.execute(
        'INSERT INTO job_searches (id, kind, label, params, enabled, interval_hours, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, 0, 24, ?, ?)',
        (new_id(), 'adzuna', 'Backend, Toronto', '{"what": "backend engineer", "where": "Toronto"}',
         ts(20), ts(20)),
    )
    db.execute(
        'INSERT INTO career_page_watches (id, url, label, known_urls, enabled, interval_hours, '
        'last_run_at, last_count, created_at, updated_at) VALUES (?, ?, ?, ?, 1, 24, ?, ?, ?, ?)',
        (new_id(), 'https://example.com/careers', 'Globex careers',
         '["https://example.com/jobs/1"]', ts(1), 1, ts(20), ts(1)),
    )
    db.execute(
        'INSERT INTO workday_boards (id, url, label, params, enabled, interval_hours, last_run_at, '
        'last_count, created_at, updated_at) VALUES (?, ?, ?, ?, 1, 24, ?, ?, ?, ?)',
        (new_id(), 'https://example.wd3.myworkdayjobs.com/External', 'Example Corp — Workday',
         '{"searchText": "backend"}', ts(1), 0, ts(20), ts(1)),
    )


def seed_chat(db):
    from backend.chat.storage import attachment_path

    conv_id = new_id()
    db.execute(
        'INSERT INTO conversations (id, title, mode, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
        (conv_id, 'Weekend trip ideas', 'chat', ts(2), ts(2)),
    )
    turns = [
        ('user', "What's a good weekend trip within a few hours of Toronto?"),
        ('assistant', "Prince Edward County is a popular pick — wineries, beaches, and about 2.5 hours away."),
    ]
    message_ids = []
    for role, content in turns:
        message_id = new_id()
        message_ids.append(message_id)
        db.execute(
            'INSERT INTO messages (id, conversation_id, role, content, status, created_at, finished_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (message_id, conv_id, role, content, 'done', ts(2), ts(2)),
        )

    db.execute(
        'INSERT INTO chat_compactions (id, conversation_id, kind, source_message_ids, '
        'content, status, carry_context, created_at, updated_at) '
        "VALUES (?, ?, 'rolling', ?, ?, 'done', 1, ?, ?)",
        (new_id(), conv_id, json.dumps(message_ids), json.dumps({
            'summary': 'The user was considering a weekend trip from Toronto.',
            'facts': [], 'decisions': [], 'openThreads': ['Choose a destination.'],
            'sources': [],
        }), ts(2), ts(2)),
    )

    # A photo the user attached, already described. 'running' is avoided —
    # _reset_stale_chat_attachment_descriptions rewrites that to 'error'.
    attachment_id = new_id()
    img_path = attachment_path(conv_id, attachment_id, 'jpg')
    placeholder_image(img_path, 'trip map')
    db.execute(
        'INSERT INTO chat_attachments (id, conversation_id, message_id, path, mime, description, '
        'description_status, latitude, longitude, position, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)',
        (attachment_id, conv_id, message_ids[0], str(img_path), 'image/jpeg',
         'A road map with a route traced east from Toronto toward the lake.',
         'done', 43.6532, -79.3832, ts(2)),
    )

    # The day-scoped todo list the chat delegate writes to, separate from the
    # Lifestyle todos.
    day_key = today_key()
    for title, done in [('Book the ferry', 0), ('Pack a cooler', 1)]:
        db.execute(
            'INSERT INTO chat_todos (id, day_key, title, notes, priority, done, completed_at, '
            'created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (new_id(), day_key, title, None, 3, done, ts(0) if done else None, ts(0), ts(0)),
        )

    # The assistant's own note queue — deliberately a separate store from the
    # user's memory document (backend/observations.py).
    for content in [
        'Prefers driving trips under three hours.',
        'Mentioned a shellfish allergy when talking about restaurants.',
    ]:
        db.execute(
            'INSERT INTO assistant_observations (id, content, source, created_at) VALUES (?, ?, ?, ?)',
            (new_id(), content, 'chat', ts(2)),
        )


def seed_writing(db):
    project_id = new_id()
    db.execute(
        'INSERT INTO writing_projects (id, title, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
        (project_id, 'Notes on lighthouse keeping', 'A short piece inspired by research on 19th-century lighthouses.', ts(12), ts(12)),
    )
    db.execute(
        'INSERT INTO writing_chapters (id, project_id, title, content, position, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, 0, ?, ?)',
        (new_id(), project_id, 'Opening', 'The keeper climbs the same stairs every night, and every night they are different.', ts(12), ts(12)),
    )
    # Context docs are what the scoped writing chat is allowed to see.
    for title, doc_type, content in [
        ('Lyra Ashworth', 'character',
         'Forty years a keeper. Speaks little, writes everything down in a ledger she never shows anyone.'),
        ('Outline', 'outline',
         '1. The storm.\n2. What the light found.\n3. The ledger is opened.'),
    ]:
        db.execute(
            'INSERT INTO writing_context_docs (id, project_id, title, content, doc_type, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (new_id(), project_id, title, content, doc_type, ts(12), ts(12)),
        )

    return project_id


def seed_ideas(db, page_id):
    # A registered repository. clone_state is 'ready' but no checkout exists
    # under REPOS_ROOT — data/repos/ is one `git clone` away and deliberately
    # excluded from backups, so the demo describes the row rather than shipping
    # a checkout. Re-clone from the Ideas tab to make the code tools live.
    repo_id = new_id()
    db.execute(
        'INSERT INTO repos (id, slug, name, remote_url, branch, clone_state, head_sha, '
        'last_pulled_at, graph_built_at, graph_node_count, is_default, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)',
        (repo_id, 'demo-app', 'Demo App', 'https://github.com/example/demo-app.git', 'main',
         'ready', '0' * 40, ts(1), ts(1), 1840, ts(20), ts(1)),
    )
    snapshot_id = new_id()
    db.execute(
        'INSERT INTO repo_snapshots (id, repo_id, git_sha, git_branch, facts, digest, '
        'change_summary, route_count, table_count, component_count, generated_at, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (snapshot_id, repo_id, '0' * 40, 'main',
         '{"routes": ["/api/health"], "tables": ["notes"], "components": ["App"]}',
         'A small Flask + React app with one API blueprint and a SQLite store.',
         'No structural change since the previous snapshot.', 1, 1, 1, ts(1), ts(1)),
    )

    # Three wiki kinds, since they render differently: an unscoped research
    # note (repo_id NULL), a code note about one module of the repo, and a life
    # article the nightly synthesis pass would have written.
    article_id = new_id()
    db.execute(
        'INSERT INTO wiki_articles (id, repo_id, slug, title, summary, content, kind, revision, '
        'created_at, updated_at) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)',
        (article_id, 'offline-first-sync', 'Offline-first sync patterns',
         'Notes on conflict-free replicated data types for a single-user app.',
         'CRDTs are likely overkill for a single-user app; a simple last-write-wins per row covers most of what this app needs.',
         'research', 2, ts(8), ts(8)),
    )
    db.execute(
        'INSERT INTO wiki_revisions (id, article_id, revision, title, content, diff, author, note, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (new_id(), article_id, 1, 'Offline-first sync patterns',
         'CRDTs are probably the answer here.',
         '- CRDTs are probably the answer here.\n+ CRDTs are likely overkill for a single-user app;…',
         'agent', 'Narrowed the claim after reading the single-writer constraint.', ts(9)),
    )
    code_article_id = new_id()
    db.execute(
        'INSERT INTO wiki_articles (id, repo_id, slug, title, summary, content, kind, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (code_article_id, repo_id, 'storage-layer', 'Storage layer',
         'How the demo app persists notes.',
         'A single SQLite file opened once per process, with schema applied at startup.',
         'code', ts(1), ts(1)),
    )

    # The life wiki: prose is rendered *from* facts and never revised into
    # itself, so every article has its citing rows beside it
    # (backend/lifewiki/CLAUDE.md).
    life_article_id = new_id()
    db.execute(
        'INSERT INTO wiki_articles (id, repo_id, slug, title, summary, content, kind, created_at, updated_at) '
        'VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?)',
        (life_article_id, 'routines', 'Routines',
         'Recurring shape of an ordinary week.',
         'Mornings start early, usually with writing before anything else. '
         'Gym three times a week, on a Monday/Wednesday/Friday rhythm.',
         'life', ts(4), ts(1)),
    )
    for statement, source_kind in [
        ('Writes in the morning before other work.', 'journal'),
        ('Goes to the gym on Mondays, Wednesdays and Fridays.', 'calendar'),
    ]:
        db.execute(
            'INSERT INTO life_facts (id, article_id, statement, source_kind, source_id, '
            'first_seen, last_seen, locked, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)',
            (new_id(), life_article_id, statement, source_kind, new_id(), ts(30), ts(1), ts(4)),
        )

    ideas = [
        ('Add a reading-streak counter to Fanfic', 'new'),
        ('Offline-first Journal entry drafts', 'researching'),
        ('Weekly digest email of the past week', 'parked'),
    ]
    researched_id = None
    for title, status in ideas:
        idea_id = new_id()
        db.execute(
            'INSERT INTO ideas (id, title, raw_content, content, status, repo_id, research_state, '
            'researched_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (idea_id, title, title, title, status, repo_id,
             'done' if status == 'researching' else 'idle',
             ts(2) if status == 'researching' else None, ts(8), ts(8)),
        )
        if status == 'researching':
            researched_id = idea_id
            db.execute(
                'INSERT INTO idea_wiki_links (idea_id, article_id, relevance, created_at) VALUES (?, ?, ?, ?)',
                (idea_id, article_id, 0.8, ts(8)),
            )

    # The evidence-backed assessment, the open questions it raised, and the
    # plan that came out of it — all hung off the one researched idea.
    assessment_id = new_id()
    db.execute(
        'INSERT INTO idea_assessments (id, idea_id, snapshot_id, verdict, confidence, rationale, '
        'evidence, on_roadmap, effort, model, assessed_at, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (assessment_id, researched_id, snapshot_id, 'partial', 0.62,
         'Drafts already survive a reload in local storage, but nothing reconciles them with the server copy.',
         '[{"file": "src/components/Journal.tsx", "line": 120, "quote": "localStorage.setItem(draftKey, text)"}]',
         'no', 'medium', 'qwen36', ts(2), ts(2)),
    )
    db.execute('UPDATE ideas SET assessment_id = ? WHERE id = ?', (assessment_id, researched_id))
    for question, key, status, answer in [
        ('Should a draft sync across devices, or stay on the one that wrote it?',
         'draft-sync-scope', 'answered', 'Stay local — this is a single-user app on one machine at a time.'),
        ('What wins when a synced draft and the server entry disagree?', 'draft-conflict', 'open', None),
    ]:
        db.execute(
            'INSERT INTO idea_questions (id, idea_id, question, question_key, why, options, answer, '
            'answered_at, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (new_id(), researched_id, question, key,
             'Changes whether this needs a server round-trip at all.',
             '["local only", "sync across devices"]', answer,
             ts(2) if answer else None, status, ts(2), ts(2)),
        )
    db.execute(
        'INSERT INTO idea_plans (id, idea_id, version, content, spec, model, snapshot_id, created_at, updated_at) '
        'VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)',
        (new_id(), researched_id,
         '## Plan\n\n1. Keep the existing localStorage draft.\n'
         '2. On mount, offer to restore it when it is newer than the saved entry.\n'
         '3. Clear it once the entry saves.',
         '{"files": ["src/components/Journal.tsx"], "tests": ["src/lib/journalDraft.test.ts"]}',
         'qwen36', snapshot_id, ts(2), ts(2)),
    )

    # A sketch links a paper page to an idea.
    db.execute(
        'INSERT INTO idea_sketches (id, idea_id, page_id, caption, position, created_at) '
        'VALUES (?, ?, ?, ?, 0, ?)',
        (new_id(), researched_id, page_id, 'Draft-restore prompt, sketched out.', ts(2)),
    )


def seed_meetings(db):
    from backend.meetings.storage import mic_path, system_path

    meeting_id = new_id()
    db.execute(
        'INSERT INTO meetings (id, title, status, phase, source, transcript_text, segments, '
        'speaker_names, summary, notes, duration_seconds, started_at, ended_at, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (meeting_id, 'Planning sync', 'done', 'done', 'live',
         "Alex: Let's ship the export feature by Friday.\nSam: Agreed, I'll own the CSV format.",
         '[{"start": 0.0, "end": 4.2, "speaker": "SPEAKER_00", '
         '"text": "Let\'s ship the export feature by Friday."}, '
         '{"start": 4.2, "end": 8.1, "speaker": "SPEAKER_01", '
         '"text": "Agreed, I\'ll own the CSV format."}]',
         '{"SPEAKER_00": "Alex", "SPEAKER_01": "Sam"}',
         'Agreed to ship the export feature by Friday; Sam owns the CSV format.',
         'Follow up on who writes the migration.',
         620.0, ts(6), ts(6), ts(6), ts(6)),
    )
    # The meetings table has no path column — the two tracks are found by
    # convention on the meeting id (backend/meetings/storage.py).
    placeholder_audio(mic_path(meeting_id))
    placeholder_audio(system_path(meeting_id))


def seed_lifestyle(db):
    today = today_key()
    for i, (title, done) in enumerate([('Read 20 minutes', 1), ('Stretch', 0), ('Drink water', 1)]):
        task_id = new_id()
        db.execute(
            'INSERT INTO daily_tasks (id, title, position, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
            (task_id, title, i, ts(20), ts(20)),
        )
        if done:
            db.execute(
                'INSERT INTO daily_task_completions (id, task_id, date, created_at) VALUES (?, ?, ?, ?)',
                (new_id(), task_id, today, ts(0)),
            )

    for title, done, lst in [
        ('Renew passport', 0, 'todo'),
        ('Book flight', 0, 'todo'),
        ('Return library books', 1, 'archive'),
    ]:
        db.execute(
            'INSERT INTO todos (id, title, done, completed_at, list, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (new_id(), title, done, ts(1) if done else None, lst, ts(5), ts(1)),
        )

    session_id = new_id()
    db.execute(
        'INSERT INTO workout_sessions (id, date, location_type, duration_minutes, intensity_rating, '
        'raw_text, parse_status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (session_id, today, 'gym', 50, 4, 'Squats 3x5 @ 185, bench 3x8 @ 135', 'done', ts(0), ts(0)),
    )
    exercise_id = new_id()
    db.execute(
        'INSERT INTO workout_exercises (id, session_id, name_raw, name_canonical, position) VALUES (?, ?, ?, ?, 0)',
        (exercise_id, session_id, 'squats', 'squat'),
    )
    for i, (weight, reps) in enumerate([(185, 5), (185, 5), (185, 5)]):
        db.execute(
            'INSERT INTO workout_sets (id, exercise_id, weight, reps, set_order) VALUES (?, ?, ?, ?, ?)',
            (new_id(), exercise_id, weight, reps, i),
        )

    db.execute(
        'INSERT INTO body_weight_logs (id, date, weight, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
        (new_id(), today, 172.4, ts(0), ts(0)),
    )

    # Daily selfie, one per date (the column is UNIQUE).
    from backend.lifestyle.storage import selfie_path

    for days_ago in (0, 1):
        date = today_key(days_ago)
        selfie_id = new_id()
        path = selfie_path(selfie_id, 'jpg')
        placeholder_image(path, date, size=(480, 640), color=(120, 100, 130))
        db.execute(
            'INSERT INTO lifestyle_selfies (id, date, path, mime, created_at) VALUES (?, ?, ?, ?, ?)',
            (selfie_id, date, str(path), 'image/jpeg', ts(days_ago)),
        )

    for days_ago in range(3):
        date = today_key(days_ago)
        db.execute(
            'INSERT INTO sleep_logs (id, date, wake_at, sleep_at, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (new_id(), date, ts(days_ago, 8), ts(days_ago + 1, -1), ts(days_ago), ts(days_ago)),
        )

    _seed_weather(db)


def _seed_weather(db):
    """The Lifestyle weather strip: a day's sunrise/sunset, its resolved
    location, and the hourly series drawn across it."""
    lat, lon = 43.6532, -79.3832
    for days_ago in (0, 1):
        day_key = today_key(days_ago)
        midnight = int(time.mktime(time.strptime(day_key, '%Y-%m-%d')))
        db.execute(
            'INSERT INTO lifestyle_weather_days (id, day_key, sunrise_ts, sunset_ts, latitude, '
            'longitude, location_source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (new_id(), day_key, midnight + 6 * 3600 + 40 * 60, midnight + 20 * 3600 + 15 * 60,
             lat, lon, 'settings', ts(days_ago), ts(days_ago)),
        )
        db.execute(
            'INSERT INTO lifestyle_weather_locations (id, day_key, latitude, longitude, source, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (new_id(), day_key, lat, lon, 'settings', ts(days_ago)),
        )
        for hour in range(0, 24, 3):
            temperature = 12.0 + 6.0 * (1 - abs(hour - 14) / 14)
            db.execute(
                'INSERT INTO lifestyle_weather_hours (id, day_key, hour_ts, weather_code, '
                'temperature_c, wet_bulb_c, humidity_pct, is_actual, latitude, longitude, '
                'location_source, created_at, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (new_id(), day_key, midnight + hour * 3600, 1 if hour < 15 else 3,
                 round(temperature, 1), round(temperature - 3.5, 1), 62.0,
                 1 if days_ago else 0, lat, lon, 'settings', ts(days_ago), ts(days_ago)),
            )


def seed_paper(db):
    from backend.paper.storage import page_image_path, pasted_image_path

    paper_id = new_id()
    db.execute(
        'INSERT INTO papers (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)',
        (paper_id, 'Meeting notes', ts(3), ts(3)),
    )
    page_id = new_id()
    snapshot_path = page_image_path(paper_id, page_id)
    placeholder_image(snapshot_path, 'page 1', size=(1000, 1400), color=(245, 245, 240))
    db.execute(
        'INSERT INTO paper_pages (id, paper_id, position, strokes, width, height, image_path, '
        'created_at, updated_at) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?)',
        (page_id, paper_id, '[]', 1000, 1400, str(snapshot_path), ts(3), ts(3)),
    )

    # A picture pasted onto the page — it keeps its own box, which is what the
    # x/y/width/height columns are (backend/paper/CLAUDE.md).
    image_id = new_id()
    pasted_path = pasted_image_path(paper_id, image_id, 'jpg')
    placeholder_image(pasted_path, 'pasted', size=(320, 240))
    db.execute(
        'INSERT INTO paper_page_images (id, page_id, file_path, x, y, width, height, rotation, '
        'flipped, locked, position, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?, ?)',
        (image_id, page_id, str(pasted_path), 120.0, 200.0, 320.0, 240.0, ts(3), ts(3)),
    )
    return page_id


def seed_email(db):
    """Two accounts and the mail the Jobs linkage sweep reads.

    Returns the ids seed_jobs needs, keyed by role rather than position — it
    links one specific message to an application, and an index would quietly
    point at a different one the moment this list grows.
    """
    from backend.email import media

    gmail_id = new_id()
    db.execute(
        'INSERT INTO email_accounts (id, provider, email_address, access_token, refresh_token, '
        'token_expires_at, scope, sync_enabled, last_synced_at, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)',
        (gmail_id, 'gmail', 'jordan.rivera@example.com', 'fake-not-a-real-access-token',
         'fake-not-a-real-refresh-token', ts(-1), 'https://www.googleapis.com/auth/gmail.readonly',
         ts(0), ts(30), ts(0)),
    )
    imap_id = new_id()
    db.execute(
        'INSERT INTO email_accounts (id, provider, email_address, imap_host, imap_port, '
        'imap_username, imap_password, uid_validity, uid_next, sync_enabled, last_synced_at, '
        'created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)',
        (imap_id, 'imap', 'jordan@example.net', 'imap.example.net', 993, 'jordan@example.net',
         'fake-not-a-real-password', 1, 4210, ts(0), ts(30), ts(0)),
    )

    messages = [
        ('confirmation', 'Thanks for applying to Globex', 'Globex Careers',
         'careers@globex.example.com',
         'We have received your application for Backend Engineer.',
         'job_application', 'sent', 4),
        ('rejection', 'Update on your Initech application', 'Initech Recruiting',
         'no-reply@initech.example.com',
         'After careful consideration we have decided to move forward with other candidates.',
         'job_application', 'rejection', 2),
        ('interview', 'Globex — next steps', 'Dana Okafor', 'dana.okafor@globex.example.com',
         'We would like to set up a technical interview next week. Are you free Tuesday?',
         'job_application', 'interview_next_step', 2),
        ('newsletter', 'This week in backend engineering', 'The Backend Weekly',
         'hello@backendweekly.example.com',
         'Five things worth reading about databases this week.',
         'newsletter', None, 1),
    ]
    email_ids = {}
    for role, subject, sender, sender_email, body, category, job_status, days_ago in messages:
        email_id = new_id()
        email_ids[role] = email_id
        db.execute(
            'INSERT INTO emails (id, account_id, provider_message_id, thread_id, subject, sender, '
            'sender_email, snippet, body_text, body_html, label_ids, received_at, category, '
            'job_status, classified_at, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (email_id, gmail_id, f'msg-{role}', f'thread-{role}', subject, sender, sender_email,
             body[:80], body, f'<p>{body}</p>', '["INBOX"]', ts(days_ago), category, job_status,
             ts(days_ago), ts(days_ago)),
        )

    # One remote image the fetcher stored, one it skipped — the two states the
    # Email view actually renders differently.
    logo = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64
    digest = media.content_hash(logo)
    path = media.path_for(digest, 'png')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(logo)
    db.execute(
        'INSERT INTO email_images (url_hash, url, content_hash, extension, content_type, '
        'byte_size, status, attempt_count, created_at, fetched_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)',
        (media.url_hash('https://backendweekly.example.com/logo.png'),
         'https://backendweekly.example.com/logo.png', digest, 'png', 'image/png',
         len(logo), 'stored', ts(1), ts(1)),
    )
    db.execute(
        'INSERT INTO email_images (url_hash, url, status, attempt_count, error, created_at) '
        'VALUES (?, ?, ?, 1, ?, ?)',
        (media.url_hash('https://tracker.example.com/pixel.gif'),
         'https://tracker.example.com/pixel.gif', 'skipped', 'Tracking pixel.', ts(1)),
    )
    return email_ids


def seed_piano(db):
    from backend.piano.storage import piano_dir

    piece_id = new_id()
    score_path = piano_dir(piece_id) / 'score.musicxml'
    placeholder_text(score_path, '<?xml version="1.0"?>\n<score-partwise version="4.0"/>\n')
    db.execute(
        'INSERT INTO piano_pieces (id, title, composer, source_filename, score_path, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (piece_id, 'Prelude in C', 'J. S. Bach', 'prelude-in-c.musicxml', str(score_path), ts(14), ts(14)),
    )
    db.execute(
        'INSERT OR REPLACE INTO piano_practice_preferences (id, session_minutes, skill_level, '
        'jazz_percent, updated_at) VALUES (1, ?, ?, ?, ?)',
        (25, 'intermediate', 40, ts(14)),
    )

    day_key = today_key()
    exercises = [
        ('scales-major', 0, 'C', 96, 5, None, None, None),
        ('ii-v-i-voicings', 1, 'F', 84, 8, None, None, None),
        ('repertoire', 2, None, None, 12, piece_id, 1, 16),
    ]
    exercise_ids = []
    for key, position, key_name, tempo, minutes, piece, m_start, m_end in exercises:
        exercise_id = new_id()
        exercise_ids.append(exercise_id)
        db.execute(
            'INSERT INTO piano_daily_exercises (id, day_key, exercise_key, position, key_name, '
            'target_tempo, minutes, piano_piece_id, measure_start, measure_end, completed_at, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (exercise_id, day_key, key, position, key_name, tempo, minutes, piece, m_start, m_end,
             ts(0) if position == 0 else None, ts(0)),
        )
    db.execute(
        'INSERT INTO piano_exercise_attempts (id, daily_exercise_id, started_at, completed_at, '
        'tempo, correct_notes, wrong_notes, onset_accuracy, duration_accuracy, tempo_stability, '
        'velocity_evenness, achieved_tempo, self_rating, notes, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (new_id(), exercise_ids[0], ts(0, 2), ts(0, 1), 96, 188, 12, 0.94, 0.88, 0.91, 0.83, 92.4,
         4, 'Left hand lagged in the descending run.', ts(0)),
    )

    # The media archive lives on the backup drive in real use; here it's a
    # scratch root (PIANO_ARCHIVE_ROOT), so the file resolve_path() looks for
    # actually exists.
    archive_root = Path(os.environ['PIANO_ARCHIVE_ROOT'])
    relative_path = 'sheet-music/prelude-in-c.pdf'
    archive_file = archive_root / relative_path
    placeholder_text(archive_file, '%PDF-1.4\n% placeholder score for the demo database\n')
    db.execute(
        'INSERT INTO media_archive_items (id, collection, title, creator, media_type, '
        'source_filename, relative_path, source_url, content_type, size_bytes, sha256, '
        'practice_compatible, favorite, piano_piece_id, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?)',
        (new_id(), 'piano', 'Prelude in C — score', 'J. S. Bach', 'file',
         'prelude-in-c.pdf', relative_path, None, 'application/pdf',
         archive_file.stat().st_size, None, piece_id, ts(14), ts(14)),
    )


def seed_practice(db):
    """Drill progress. `snippet_id` is not a foreign key — the snippet bank is a
    git-versioned list in backend/practice/snippets.py — so the ids are taken
    from it rather than invented, or the Practice tab shows progress for
    snippets that don't exist."""
    from backend.practice.snippets import SNIPPETS

    snippet_ids = [s['id'] for s in SNIPPETS[:3]]
    for i, snippet_id in enumerate(snippet_ids):
        db.execute(
            'INSERT INTO practice_progress (snippet_id, attempts_count, last_wpm, last_accuracy, '
            'best_wpm, best_accuracy, last_practiced_at, recall_attempts_count, recall_passes, '
            'last_recall_passed, last_recall_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (snippet_id, 3 + i, 48.0 + i, 0.96, 54.0 + i, 0.99, ts(i), 2, 1,
             1 if i == 0 else 0, ts(i), ts(i)),
        )
        db.execute(
            'INSERT INTO practice_attempts (id, snippet_id, wpm, accuracy, error_count, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (new_id(), snippet_id, 48.0 + i, 0.96, 2, ts(i)),
        )
    db.execute(
        'INSERT INTO practice_recall_attempts (id, snippet_id, submitted, verdict, passed, '
        'feedback, graded_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (new_id(), snippet_ids[0], "const [count, setCount] = useState(0);", 'pass', 1,
         'Correct, though the import line was missing.', 'model', ts(0)),
    )


def seed_notes(db):
    """Notes to self — a short spaced-repetition queue over the user's own
    reminders, with the revision history a note edit leaves behind."""
    # The first is due now, so /api/notes/due actually surfaces something; a
    # queue where everything is scheduled for later renders as an empty view.
    notes = [
        ('The squeaky drawer needs a shim, not oil.', 3, -1),
        ('Passport expires in November — renew before booking anything.', 7, 4),
    ]
    for content, interval_days, due_in in notes:
        note_id = new_id()
        db.execute(
            'INSERT INTO notes_to_self (id, content, interval_days, due, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (note_id, content, interval_days, ts(-due_in), ts(10), ts(2)),
        )
        db.execute(
            'INSERT INTO note_to_self_revisions (id, note_id, content, created_at) VALUES (?, ?, ?, ?)',
            (new_id(), note_id, content.replace('.', '', 1), ts(10)),
        )


def seed_notebook(db, journal_ids):
    """Markdown files under NOTEBOOK_ROOT plus their review state. The path
    column is relative to the root (backend/routes/notebook.py), so the files
    have to exist for the rows to resolve."""
    root = Path(os.environ['NOTEBOOK_ROOT'])
    pages = [
        ('diary/2024-05-01.md', '# May 1\n\nRained all day. Read on the porch anyway.\n', 1),
        ('reference/knots.md', '# Knots\n\n- Bowline: a loop that will not slip.\n- Clove hitch.\n', 1),
        ('scratch/ideas.md', '# Scratch\n\nHalf-formed things that are not ideas yet.\n', 0),
    ]
    for rel, body, enabled in pages:
        placeholder_text(root / rel, body)
        db.execute(
            'INSERT INTO notebook_review_state (path, enabled, fsrs_state, due, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (rel, enabled,
             '{"stability": 4.2, "difficulty": 5.1, "reps": 3}' if enabled else None,
             ts(-2) if enabled else None, ts(12), ts(2)),
        )
    db.execute(
        'INSERT INTO notebook_diary_promotions (date, journal_entry_id, promoted_at) VALUES (?, ?, ?)',
        ('2024-05-01', journal_ids[0], ts(6)),
    )


def seed_study(db):
    """Study sources plus the notebook notes they are bound to.

    Three rows on purpose: they cover all three `kind`s and both terminal
    `import_status` values, so the library, the desk and the failure state are
    all reachable in the demo. The two `ready` rows get real files, because the
    left pane parses what it is given — pdf.js for the PDF, a sandboxed iframe
    for the article.

    The YouTube row is deliberately `error`, not `ready`: seeding a playable
    video would mean shipping one (or depending on ffmpeg being installed), and
    a `ready` row pointing at bytes that do not exist demos worse than the
    failure UI does. `importing` is not an option — it is an in-flight state and
    `_reset_stale_study_imports` would rewrite it on the next start.

    Its title and duration are still filled in, which is the honest shape of
    that failure: yt-dlp's metadata pass succeeded and the download did not.

    That choice has since acquired a second justification. Videos live on the
    external archive drive and nowhere else, so a `ready` video row would mean
    a seed run writing to `STUDY_ARCHIVE_ROOT` — and a seeder that touches the
    archive is a seeder that can touch the real one when an env var is missed.

    The article row is left in `note_mode='paper'` so the demo opens on the
    handwriting half rather than only ever on the Notebook one. Its paper is an
    ordinary `papers` row, which is the whole point of borrowing rather than
    modelling: it is also listed in the Paper tab.
    """
    from backend.paper.storage import page_image_path
    from backend.study.storage import source_file_path

    notebook_root = Path(os.environ['NOTEBOOK_ROOT'])

    pdf_id, web_id, video_id = str(ULID()), str(ULID()), str(ULID())

    pdf_note = 'study/attention-is-all-you-need.md'
    placeholder_text(
        notebook_root / pdf_note,
        '# Attention Is All You Need\n\n'
        '- Self-attention replaces recurrence entirely.\n'
        '- Positional encoding is what puts order back in.\n'
        '- See also [[reference/knots]] for how a link renders.\n',
    )
    pdf_path = source_file_path(pdf_id, 'book', 'pdf', 'pdf')
    placeholder_pdf(pdf_path, ['Attention Is All You Need', '2. Background', '3. Model Architecture'])

    web_note = 'study/wal-mode.md'
    placeholder_text(
        notebook_root / web_note,
        '# WAL mode\n\n- Readers do not block the writer.\n- One writer at a time.\n',
    )
    web_path = source_file_path(web_id, 'article', 'html', 'web')
    placeholder_text(
        web_path,
        '<h1>Write-Ahead Logging</h1>\n'
        '<p>WAL mode permits many simultaneous readers and one writer.</p>\n'
        '<p>The rollback journal is replaced by an append-only log.</p>\n',
    )

    # `position` means whatever the row's `kind` says it means: page 2 of the
    # PDF, seconds into the video. The web row leaves it null, which is the
    # permanent state for an article — a sandboxed iframe's scroll is
    # unreadable from outside it.
    # A handwriting paper for the article row, created the way the desk creates
    # one: an ordinary paper with a single blank page. The snapshot matters —
    # it is the whole of what the Paper explorer grid shows.
    study_paper_id = new_id()
    db.execute(
        'INSERT INTO papers (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)',
        (study_paper_id, 'Write-Ahead Logging — worked through', ts(3), ts(3)),
    )
    study_page_id = new_id()
    study_snapshot = page_image_path(study_paper_id, study_page_id)
    placeholder_image(study_snapshot, 'WAL, by hand', size=(1000, 1400),
                      color=(245, 245, 240))
    db.execute(
        'INSERT INTO paper_pages (id, paper_id, position, strokes, width, height, image_path, '
        'created_at, updated_at) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?)',
        (study_page_id, study_paper_id, '[]', 1000, 1400, str(study_snapshot),
         ts(3), ts(3)),
    )

    rows = [
        # id, title, kind, source_url, file_path, content_type, size_bytes,
        # duration, note_path, paper_id, note_mode, status, error,
        # last_opened_at, position
        (pdf_id, 'Attention Is All You Need', 'pdf', None, str(pdf_path),
         'application/pdf', pdf_path.stat().st_size, None, pdf_note, None, 'note',
         'ready', None, ts(1), 2),
        (web_id, 'Write-Ahead Logging', 'web', 'https://www.sqlite.org/wal.html',
         str(web_path), 'text/html', web_path.stat().st_size, None, web_note,
         study_paper_id, 'paper', 'ready', None, ts(3), None),
        (video_id, 'Backpropagation, step by step', 'youtube',
         'https://www.youtube.com/watch?v=Ilg3gGewQ5U', None, None, 0, 501,
         None, None, 'note', 'error',
         'ERROR: Requested format is not available', None, None),
    ]
    for row in rows:
        db.execute(
            'INSERT INTO study_sources (id, title, kind, source_url, file_path, content_type, '
            'size_bytes, duration_seconds, note_path, paper_id, note_mode, import_status, '
            'import_error, last_opened_at, position, created_at, updated_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (*row, ts(20), ts(1)),
        )


def seed_files():
    """The Files/Editor tab is pure filesystem — it has no table of its own, so
    an otherwise fully-seeded demo still opens it on an empty tree. Writes a
    small tree under FILES_ROOT instead (backend/files_config.py)."""
    root = Path(os.environ['FILES_ROOT'])
    files = {
        'README.md': (
            '# Notes\n\nThis is the Files tab, pointed at a scratch directory by '
            'test-env.sh.\nEdit anything here — it is throwaway demo content.\n'
        ),
        'projects/lighthouse.md': (
            '# Lighthouse piece\n\n- [ ] Finish the storm chapter\n'
            '- [x] Research 19th-century keeper logs\n'
        ),
        'projects/todo.md': '# Todo\n\n- Renew passport\n- Book flight\n',
        'snippets/sqlite.md': (
            '# SQLite\n\n```sql\nPRAGMA journal_mode=WAL;\n```\n\n'
            'WAL lets one writer and many readers coexist.\n'
        ),
        'snippets/hello.py': 'def main():\n    print("hello")\n\n\nif __name__ == "__main__":\n    main()\n',
    }
    for rel, body in files.items():
        placeholder_text(root / rel, body)


def seed_memory(db):
    """The one standing document about the user, plus the revisions every write
    snapshots (backend/memory.py). Nothing but Settings → Memory writes it."""
    content = (
        "Lives in Toronto. Writes in the mornings.\n\n"
        "Names that dictation tends to mangle: Lyra Ashworth (a character), "
        "Ashworth Bay (the setting), Initech and Globex (companies applied to).\n\n"
        "Prefers short, direct answers."
    )
    db.execute(
        'INSERT OR REPLACE INTO user_memory (id, content, updated_at) VALUES (1, ?, ?)',
        (content, ts(2)),
    )
    for revision_content, source, days_ago in [
        ('Lives in Toronto. Writes in the mornings.', 'user', 20),
        (content, 'user', 2),
    ]:
        db.execute(
            'INSERT INTO user_memory_revisions (id, content, source, note, created_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (new_id(), revision_content, source, None, ts(days_ago)),
        )


def seed_infra(db):
    """Settings, MCP servers, the transcription log and the task-event feed.

    Every credential column stays empty or obviously fake: this script ships in
    a public repo, and a demo database is exactly the sort of file someone
    copies without reading.
    """
    db.execute(
        'UPDATE settings SET ai_provider = ?, llama_url = ?, llama_model = ?, '
        'stt_backend = ?, tts_backend = ?, whisper_model = ?, stt_device = ?, '
        'voice_pipeline_enabled = 0, nudge_enabled = 0, briefing_enabled = 0, '
        "stt_screenshot_key = '', "
        'email_sync_enabled = 0, research_enabled = 0, jobs_paused = 1, '
        # Never seed a paused demo: the switch lives at the top of Settings and
        # an instance that boots with inference off looks broken, not idle.
        'inference_paused = 0, '
        'weather_default_lat = ?, weather_default_lon = ?, weather_default_label = ?, '
        'backup_retention_days = 14, llm_thinking = 0, updated_at = ? WHERE id = 1',
        ('llama', 'http://localhost:8080', 'qwen36', 'faster-whisper', 'kokoro', 'base', 'cpu',
         43.6532, -79.3832, 'Toronto, ON', ts(0)),
    )
    # files_root is deliberately left empty so backend/routes/files.py falls
    # through to the FILES_ROOT env var test-env.sh exports — otherwise the
    # demo database would pin one machine's absolute path.

    # The durable queue for background model work. A `done` row and an `error`
    # row, never a `running` one — that is an in-flight state init_db()'s orphan
    # reset rewrites on the next start (see the seeding rules in CLAUDE.md). A
    # `pending` row is safe and worth having: it is what the Settings panel's
    # "waiting" count is drawn from.
    db.execute(
        'INSERT INTO llm_jobs (id, kind, target_id, payload, status, attempts, '
        'created_at, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (new_id(), 'journal.polish', new_id(), '{"raw_content": "a dictated note"}',
         'done', 1, ts(2), ts(2), ts(2)),
    )
    db.execute(
        'INSERT INTO llm_jobs (id, kind, target_id, payload, status, attempts, '
        'error, created_at, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (new_id(), 'food.structure', new_id(), '{"text": "two eggs and toast"}',
         'error', 1, 'model returned an empty response', ts(1), ts(1), ts(1)),
    )
    db.execute(
        'INSERT INTO llm_jobs (id, kind, target_id, payload, status, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (new_id(), 'calendar.classify', new_id(), '{}', 'pending', ts(0)),
    )

    db.execute(
        'INSERT INTO mcp_servers (id, name, transport, command, args, env, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (new_id(), 'filesystem', 'stdio', 'npx',
         '["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]', '{}', ts(20), ts(20)),
    )
    db.execute(
        'INSERT INTO mcp_servers (id, name, transport, url, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (new_id(), 'search', 'http', 'http://localhost:8931/mcp', ts(20), ts(20)),
    )

    for text, source, app, days_ago in [
        ('Note to self, the drawer needs a shim not oil.', 'journal', 'lunaschal', 1),
        ('what is a good weekend trip within a few hours of toronto', 'voice', 'lunaschal', 2),
        ('squats three by five at one eighty five, bench three by eight at one thirty five',
         'paste', 'firefox', 0),
    ]:
        db.execute(
            'INSERT INTO transcriptions (id, text, source, app, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)',
            (new_id(), text, source, app, None, ts(days_ago)),
        )

    for kind, title, task_list, days_ago in [
        ('completed', 'Return library books', 'todo', 1),
        ('created', 'Book flight', 'todo', 5),
        ('completed', 'Read 20 minutes', 'daily', 0),
    ]:
        db.execute(
            'INSERT INTO task_events (id, kind, title, ref_id, task_list, detail, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (new_id(), kind, title, None, task_list, None, ts(days_ago)),
        )


def main() -> None:
    print('Wiping scratch DB and media directories...')
    wipe_scratch()

    print(f'Initializing schema at {os.environ["DATABASE_URL"]}...')
    connection.init_db()
    db = connection.get_db()

    # Order is a dependency order, not a preference: fanfic references journal
    # entries, jobs reference emails, and ideas hang a sketch off a paper page.
    print('Seeding data...')
    journal_ids = seed_journal(db)
    seed_calendar(db, journal_ids)
    seed_learning(db)
    recipe_id = seed_cookbook(db)
    seed_food(db, recipe_id)
    seed_fanfic(db, journal_ids)
    seed_newspapers(db)
    seed_torrents(db)
    email_ids = seed_email(db)
    seed_jobs(db, email_ids)
    seed_chat(db)
    seed_writing(db)
    page_id = seed_paper(db)
    seed_ideas(db, page_id)
    seed_meetings(db)
    seed_lifestyle(db)
    seed_piano(db)
    seed_practice(db)
    seed_notes(db)
    seed_notebook(db, journal_ids)
    # After seed_notebook: a study source is bound to a note under the same
    # NOTEBOOK_ROOT, so it writes into the tree that one has just created.
    seed_study(db)
    seed_files()
    seed_memory(db)
    seed_infra(db)

    db.commit()

    empty = _unseeded_tables(db)
    if empty:
        print(
            f'WARNING: {len(empty)} table(s) have no seeded row — '
            'backend/tests/test_seed_test_db.py will fail:\n  ' + '\n  '.join(empty)
        )

    db.close()
    connection._conn = None
    print('Done.')


def _unseeded_tables(db) -> list[str]:
    """Every table that came out of this run empty.

    The demo's whole promise is that no view renders blank, and a table added
    to schema.sql later would break that silently — so the check is derived
    from sqlite_master rather than from a list kept by hand here. The six
    external-content FTS5 tables and their shadows are excluded: they are
    trigger-maintained, and writing to them directly is a corruption bug.
    """
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%\\_fts%' ESCAPE '\\' "
        'ORDER BY name'
    ).fetchall()
    return [
        r['name'] for r in rows
        if db.execute(f'SELECT COUNT(*) c FROM "{r["name"]}"').fetchone()['c'] == 0
    ]


if __name__ == '__main__':
    main()
