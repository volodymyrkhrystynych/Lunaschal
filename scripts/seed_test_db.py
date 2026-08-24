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
import os
import shutil
import sys
import time
from pathlib import Path

REQUIRED_ENV_VARS = [
    'DATABASE_URL', 'FANFIC_ROOT', 'MEETINGS_ROOT', 'JOURNAL_ROOT',
    'JOURNAL_DRAFTS_ROOT', 'LIFESTYLE_ROOT', 'FOOD_ROOT', 'RECIPE_ROOT',
    'CHAT_ROOT', 'PAPER_ROOT', 'JOBS_ROOT', 'NEWSPAPERS_ROOT',
    'NOTEBOOK_ROOT', 'EMAIL_MEDIA_ROOT', 'SHORTCUTS_PATH',
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

from backend.db import connection  # noqa: E402
from backend.tags import tags_json  # noqa: E402

DAY = 86400


def new_id() -> str:
    return str(ULID())


def ts(days_ago: int = 0, hours_ago: int = 0) -> int:
    return int(time.time()) - days_ago * DAY - hours_ago * 3600


def placeholder_image(path: Path, label: str, size=(640, 400), color=(90, 110, 140)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new('RGB', size, color)
    draw = ImageDraw.Draw(img)
    draw.text((size[0] // 2, size[1] // 2), label, fill=(255, 255, 255), anchor='mm')
    img.save(path, 'JPEG', quality=80)


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
    db.execute(
        'INSERT INTO calendar_events '
        '(id, title, description, date, time, all_day, tags, created_at, repeat_freq, repeat_interval, repeat_byweekday) '
        'VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)',
        (new_id(), 'Gym', 'Leg day', today, '07:00', tags_json(['exercise']), ts(0), 'weekly', 1, '1,3,5'),
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
    entries = [
        (None, 'Lentil soup', 'Home', 5, ts(1)),
        (recipe_id, 'Lentil soup, leftovers', 'Home', 4, ts(0)),
        (None, 'Pad thai', 'Thai place downtown', 4, ts(3)),
    ]
    for rid, dish, place, rating, when in entries:
        db.execute(
            'INSERT INTO food_entries (id, dish, place, rating, recipe_id, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (new_id(), dish, place, rating, rid, when, when),
        )


def seed_fanfic(db):
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


def seed_newspapers(db):
    from backend.newspapers.storage import build_path

    today = time.strftime('%Y-%m-%d')
    for paper in ('toronto-star', 'nyt'):
        path = build_path(paper, today, 'image/jpeg')
        placeholder_image(path, paper, size=(800, 1000))
        db.execute(
            'INSERT INTO newspaper_frontpages (id, paper, date, image_path, created_at) VALUES (?, ?, ?, ?, ?)',
            (new_id(), paper, today, str(path), ts(0)),
        )


def seed_jobs(db):
    # init_db()'s migrations already seed a default job_profile row (id=1,
    # same singleton idiom as `settings`) — fill it in rather than insert.
    db.execute(
        'UPDATE job_profile SET full_name = ?, email = ?, phone = ?, location = ?, '
        'headline = ?, summary = ?, updated_at = ? WHERE id = 1',
        ('Jordan Rivera', 'jordan.rivera@example.com', '555-0100', 'Toronto, ON',
         'Backend engineer', 'Backend engineer with 6 years building data-heavy web services.',
         ts(30)),
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

    job_id = new_id()
    db.execute(
        'INSERT INTO jobs (id, source, source_id, url, company, title, location, remote, description, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)',
        (job_id, 'manual', job_id, 'https://example.com/jobs/1', 'Globex', 'Backend Engineer',
         'Remote', 'Own the payments API and its on-call.', ts(5), ts(5)),
    )
    db.execute(
        'INSERT INTO applications (id, job_id, status, applied_email, applied_at, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (new_id(), job_id, 'interview', 'jordan.rivera@example.com', ts(4), ts(5), ts(2)),
    )


def seed_chat(db):
    conv_id = new_id()
    db.execute(
        'INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)',
        (conv_id, 'Weekend trip ideas', ts(2), ts(2)),
    )
    turns = [
        ('user', "What's a good weekend trip within a few hours of Toronto?"),
        ('assistant', "Prince Edward County is a popular pick — wineries, beaches, and about 2.5 hours away."),
    ]
    for role, content in turns:
        db.execute(
            'INSERT INTO messages (id, conversation_id, role, content, status, created_at, finished_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (new_id(), conv_id, role, content, 'done', ts(2), ts(2)),
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


def seed_ideas(db):
    article_id = new_id()
    db.execute(
        'INSERT INTO wiki_articles (id, slug, title, summary, content, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (article_id, 'offline-first-sync', 'Offline-first sync patterns',
         'Notes on conflict-free replicated data types for a single-user app.',
         'CRDTs are likely overkill for a single-user app; a simple last-write-wins per row covers most of what this app needs.',
         ts(8), ts(8)),
    )
    ideas = [
        ('Add a reading-streak counter to Fanfic', 'new'),
        ('Offline-first Journal entry drafts', 'researching'),
        ('Weekly digest email of the past week', 'parked'),
    ]
    for title, status in ideas:
        idea_id = new_id()
        db.execute(
            'INSERT INTO ideas (id, title, raw_content, content, status, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (idea_id, title, title, title, status, ts(8), ts(8)),
        )
        if status == 'researching':
            db.execute(
                'INSERT INTO idea_wiki_links (idea_id, article_id, relevance, created_at) VALUES (?, ?, ?, ?)',
                (idea_id, article_id, 0.8, ts(8)),
            )


def seed_meetings(db):
    db.execute(
        'INSERT INTO meetings (id, title, status, phase, source, transcript_text, summary, '
        'duration_seconds, started_at, ended_at, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (new_id(), 'Planning sync', 'done', 'done', 'live',
         "Alex: Let's ship the export feature by Friday.\nSam: Agreed, I'll own the CSV format.",
         'Agreed to ship the export feature by Friday; Sam owns the CSV format.',
         620.0, ts(6), ts(6), ts(6), ts(6)),
    )


def seed_lifestyle(db):
    today = time.strftime('%Y-%m-%d')
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


def seed_paper(db):
    paper_id = new_id()
    db.execute(
        'INSERT INTO papers (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)',
        (paper_id, 'Meeting notes', ts(3), ts(3)),
    )
    db.execute(
        'INSERT INTO paper_pages (id, paper_id, position, strokes, created_at, updated_at) '
        'VALUES (?, ?, 0, ?, ?, ?)',
        (new_id(), paper_id, '[]', ts(3), ts(3)),
    )


def main() -> None:
    print('Wiping scratch DB and media directories...')
    wipe_scratch()

    print(f'Initializing schema at {os.environ["DATABASE_URL"]}...')
    connection.init_db()
    db = connection.get_db()

    print('Seeding data...')
    journal_ids = seed_journal(db)
    seed_calendar(db, journal_ids)
    seed_learning(db)
    recipe_id = seed_cookbook(db)
    seed_food(db, recipe_id)
    seed_fanfic(db)
    seed_newspapers(db)
    seed_jobs(db)
    seed_chat(db)
    seed_writing(db)
    seed_ideas(db)
    seed_meetings(db)
    seed_lifestyle(db)
    seed_paper(db)

    db.commit()
    db.close()
    connection._conn = None
    print('Done.')


if __name__ == '__main__':
    main()
