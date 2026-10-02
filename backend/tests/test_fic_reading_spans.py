"""Reading spans: the reader's record of when a chapter was open and scrolled,
and how the briefing's day reconstruction reads it as evidence."""
import pytest

from backend.briefing_events import RECONSTRUCTION_PROMPT, gather_day, stage_events
from backend.db.connection import get_db
from backend.day_boundary import day_bounds
from backend.tests.test_fanfic_routes import fanfic_root, make_fic  # noqa: F401

DAY = '2026-07-13'
TODAY = '2026-07-14'
START, END = day_bounds(DAY)


def span(client, fic_id, chapter_id, span_id='span1', **changes):
    body = {'chapterId': chapter_id, 'startedAt': START + 3600, 'endedAt': START + 3900,
            'activeSeconds': 280, 'startFraction': 0.1, 'endFraction': 0.4, **changes}
    return client.put(f'/api/fanfic/{fic_id}/reading-spans/{span_id}', json=body)


def rows():
    return [dict(r) for r in get_db().execute('SELECT * FROM fic_reading_spans ORDER BY started_at')]


def test_heartbeats_only_move_a_span_forward(client):
    fic_id, (ch,) = make_fic(chapters=[('One', 'text')])
    assert span(client, fic_id, ch).status_code == 200
    assert span(client, fic_id, ch, endedAt=START + 4500, activeSeconds=850, endFraction=0.9).status_code == 200
    # A stale flush replayed from the offline queue after the newer one.
    assert span(client, fic_id, ch).status_code == 200
    (row,) = rows()
    assert (row['started_at'], row['ended_at'], row['active_seconds']) == (START + 3600, START + 4500, 850)
    assert row['start_fraction'] == pytest.approx(0.1)
    assert row['end_fraction'] == pytest.approx(0.9)


def test_fractions_are_clamped(client):
    fic_id, (ch,) = make_fic(chapters=[('One', 'text')])
    assert span(client, fic_id, ch, startFraction=-1, endFraction=3).status_code == 200
    (row,) = rows()
    assert (row['start_fraction'], row['end_fraction']) == (0.0, 1.0)


def test_chapter_must_belong_to_fic_and_span_to_chapter(client):
    fic_id, (one, two) = make_fic(chapters=[('One', 'a'), ('Two', 'b')])
    other_fic, (foreign,) = make_fic(chapters=[('X', 'c')])
    assert span(client, fic_id, foreign).status_code == 404
    assert span(client, fic_id, one).status_code == 200
    assert span(client, fic_id, two).status_code == 409
    assert len(rows()) == 1


@pytest.mark.parametrize('changes', [
    {'chapterId': None},
    {'startedAt': 'soon'},
    {'activeSeconds': True},
    {'endedAt': START + 3000},                       # ends before it starts
    {'endedAt': START + 3600 + 2 * 86400},           # longer than any span
    {'activeSeconds': 300 + 181},                    # more than the wall span allows
    {'activeSeconds': -1},
    {'endFraction': 'half'},
])
def test_bad_bodies_are_rejected(client, changes):
    fic_id, (ch,) = make_fic(chapters=[('One', 'text')])
    assert span(client, fic_id, ch, **changes).status_code == 400
    assert rows() == []


def test_future_spans_are_rejected(client):
    import time
    fic_id, (ch,) = make_fic(chapters=[('One', 'text')])
    now = int(time.time())
    assert span(client, fic_id, ch, startedAt=now + 3600, endedAt=now + 3700, activeSeconds=60).status_code == 400


def test_deleting_the_fic_removes_its_spans(client):
    fic_id, (ch,) = make_fic(chapters=[('One', 'text')])
    span(client, fic_id, ch)
    db = get_db()
    db.execute('DELETE FROM fics WHERE id=?', (fic_id,))
    db.commit()
    assert rows() == []


def test_gather_day_reports_scrolled_chapters_as_reading(client):
    fic_id, (one, two) = make_fic(title='Lighthouse', chapters=[('Arrival', 'a'), ('Storm', 'b')])
    span(client, fic_id, one, 'in', startedAt=START + 17 * 3600, endedAt=START + 17 * 3600 + 1500,
         activeSeconds=1400, startFraction=0, endFraction=1)
    span(client, fic_id, two, 'short', startedAt=START + 18 * 3600, endedAt=START + 18 * 3600 + 40,
         activeSeconds=40)
    span(client, fic_id, two, 'before', startedAt=START - 4000, endedAt=START - 3000, activeSeconds=900)
    span(client, fic_id, two, 'after', startedAt=END, endedAt=END + 900, activeSeconds=900)
    # Started just before 4am and read past it: still part of the day.
    span(client, fic_id, two, 'across', startedAt=START - 600, endedAt=START + 600, activeSeconds=1100)

    ctx = gather_day(get_db(), TODAY)
    reading = {s['sourceId']: s for s in ctx['sources'] if s['sourceId'].startswith('reading:')}
    assert set(reading) == {'reading:in', 'reading:across'}
    item = reading['reading:in']
    assert item['story'] == 'Lighthouse' and item['chapter'] == 'Arrival'
    assert item['title'] == 'Lighthouse — Arrival'
    assert item['activeMinutes'] == 23
    assert (item['scrolledFrom'], item['scrolledTo']) == (0, 1)
    assert item['recordedAt'] == item['startedAt']
    assert 'reading' in RECONSTRUCTION_PROMPT and 'scrolled' in RECONSTRUCTION_PROMPT


def test_a_reading_block_can_be_staged_from_spans_alone(client):
    fic_id, (one, two) = make_fic(title='Lighthouse', chapters=[('Arrival', 'a'), ('Storm', 'b')])
    span(client, fic_id, one, 'a', startedAt=START + 17 * 3600, endedAt=START + 17 * 3600 + 1500,
         activeSeconds=1400)
    span(client, fic_id, two, 'b', startedAt=START + 17 * 3600 + 1600, endedAt=START + 18 * 3600 + 1800,
         activeSeconds=3500)
    db = get_db()
    ctx = gather_day(db, TODAY)
    staged = stage_events(db, [{
        'title': 'Leisure reading: Lighthouse', 'description': 'Two chapters', 'date': DAY,
        'time': '21:00', 'endTime': '22:30', 'location': '', 'tags': ['leisure'],
        'evidence': 'Reader scroll spans 21:00–22:30.', 'sourceIds': ['reading:a', 'reading:b'],
    }], ctx)
    assert len(staged) == 1
    assert [s['label'] for s in staged[0]['sources']] == ['Lighthouse — Arrival', 'Lighthouse — Storm']
