"""The day's score is arithmetic (backend/coach/review_score.py)."""
import pytest

from backend.coach import review_score as rs


def item(kind='habit', id='h1', title='Stretch', done=True):
    return {'kind': kind, 'id': id, 'title': title, 'done': done}


def excuse(target_kind='habit', target_id='h1', kind='external', reason='Sick', day='2026-11-02', obstacle=None):
    return {'target_kind': target_kind, 'target_id': target_id, 'kind': kind, 'reason': reason,
            'day': day, 'obstacle': obstacle, 'target_title': 'Stretch'}


@pytest.mark.parametrize('value,rating', [
    (None, None), (0.0, 1), (0.24, 1), (0.25, 2), (0.5, 3), (0.69, 3), (0.7, 4), (0.89, 4), (0.9, 5), (1.0, 5),
])
def test_rating_bands(value, rating):
    assert rs.rating_for(value) == rating


def test_excused_items_leave_the_score_and_unknowns_never_count():
    items = rs.apply_excuses([
        item(id='h1', done=False), item(id='h2', done=True), item(kind='weight', id=None, done=False),
        item(kind='goal', id='g', done=None),
    ], [excuse(target_id='h1'), excuse(target_kind='weight', target_id=None, reason='Away')])
    scored = rs.score(items)
    assert (scored['done'], scored['counted'], scored['excused']) == (1, 1, 2)
    assert scored['rating'] == 5
    assert items[0]['excuse'] == 'Sick' and items[0]['excused']


def test_an_internal_reason_is_kept_but_does_not_excuse():
    [shown] = rs.apply_excuses([item(done=False)], [excuse(kind='internal', reason='Skipped it')])
    assert shown['excuse'] == 'Skipped it' and not shown['excused']
    assert rs.score([shown])['rating'] == 1


def test_a_whole_day_excuse_excuses_everything():
    items = rs.apply_excuses([item(done=False), item(kind='calories', id=None, done=False)],
                             [excuse(target_kind='day', target_id=None, kind='one_off')])
    assert all(i['excused'] for i in items)
    assert rs.score(items)['rating'] is None


def test_override_is_one_star_at_most():
    assert rs.clamp_override(3, 4) == 4
    assert rs.clamp_override(3, 2) == 2
    with pytest.raises(ValueError, match='one star'):
        rs.clamp_override(3, 5)
    with pytest.raises(ValueError):
        rs.clamp_override(None, 3)


def test_recurring_obstacles_need_three_days_in_two_weeks():
    seen = [excuse(day=d, kind='internal', obstacle='Late meetings') for d in ('2026-11-01', '2026-11-03')]
    assert rs.recurring(seen, '2026-11-04') == []
    seen.append(excuse(day='2026-11-04', kind='internal', obstacle='late  MEETINGS'))
    [found] = rs.recurring(seen, '2026-11-04')
    assert found['days'] == 3 and found['targetId'] == 'h1'
    # The same obstacle three weeks ago is outside the window.
    old = [excuse(day=d, obstacle='Late meetings') for d in ('2026-10-01', '2026-10-02', '2026-10-03')]
    assert rs.recurring(old, '2026-11-04') == []
    # Two excuses on one day are one day.
    same = [excuse(day='2026-11-04', obstacle='x')] * 3
    assert rs.recurring(same, '2026-11-04') == []


def test_stars():
    assert rs.stars(3) == '★★★☆☆'
    assert rs.stars(None) == ''
