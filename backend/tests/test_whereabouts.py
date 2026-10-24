"""Matching a day's visits to Home and Work: pure, no DB."""
from datetime import datetime

from backend.day_boundary import day_bounds
from backend.location import whereabouts
from backend.location.trail import Fix
from backend.place_roles import RolePlace

DAY = '2026-07-08'
BOUNDS = day_bounds(DAY)
HOME = RolePlace('home', 'p-home', 'Home', 43.6677, -79.3948, 150)
OFFICE = RolePlace('work', 'p-office', 'Office', 43.6532, -79.3832, 150)
COWORK = RolePlace('work', 'p-cowork', 'Cowork', 43.6600, -79.3900, 150)


def at(hhmm: str, date: str = DAY) -> float:
    return datetime.fromisoformat(f'{date}T{hhmm}:00').timestamp()


def visit(arrive, depart, place=HOME, accuracy=20.0, dlat=0.0):
    return Fix(arrive, place.lat + dlat, place.lon, 'visit', accuracy, depart)


NOW = at('23:59')


def test_time_at_home_and_work_is_summed_and_clipped_to_the_4am_day():
    fixes = [
        visit(at('22:00', '2026-07-07'), at('08:30')),  # the night before: only 04:00-08:30 counts
        visit(at('09:15'), at('17:45'), OFFICE),
        visit(at('18:30'), None),                        # home for the evening, still there
    ]
    summary = whereabouts.summarize(whereabouts.stays(fixes, [HOME, OFFICE], NOW), BOUNDS, NOW)
    assert summary['home']['seconds'] == int(4.5 * 3600) + int(NOW - at('18:30'))
    assert summary['home']['here'] is True
    assert datetime.fromisoformat(summary['home']['lastArrival']).timestamp() == at('18:30')
    [office] = summary['work']
    assert office['name'] == 'Office' and office['seconds'] == int(8.5 * 3600)
    assert datetime.fromisoformat(office['firstArrival']).timestamp() == at('09:15')
    assert datetime.fromisoformat(office['lastDeparture']).timestamp() == at('17:45')


def test_several_works_are_reported_separately_by_time_spent():
    fixes = [visit(at('09:00'), at('10:00'), COWORK), visit(at('11:00'), at('16:00'), OFFICE)]
    summary = whereabouts.summarize(whereabouts.stays(fixes, [OFFICE, COWORK], NOW), BOUNDS, NOW)
    assert [w['name'] for w in summary['work']] == ['Office', 'Cowork']
    assert summary['home'] is None


def test_nearest_place_wins_when_radii_overlap():
    near_home = RolePlace('work', 'p-next-door', 'Next door', HOME.lat + 0.001, HOME.lon, 500)
    fix = visit(at('09:00'), at('12:00'))
    assert whereabouts.match(fix, [near_home, HOME]) is HOME


def test_a_visit_outside_every_radius_has_no_role():
    stays = whereabouts.stays([visit(at('09:00'), at('12:00'), dlat=0.01)], [HOME], NOW)
    assert stays[0].place is None
    assert whereabouts.summarize(stays, BOUNDS, NOW) is None
    assert whereabouts.describe(stays)[0]['role'] is None


def test_short_and_inaccurate_visits_are_not_counted():
    fixes = [visit(at('09:00'), at('09:02')), visit(at('10:00'), at('12:00'), accuracy=5000)]
    assert whereabouts.stays(fixes, [HOME], NOW) == []


def test_no_roles_means_no_match():
    stays = whereabouts.stays([visit(at('09:00'), at('12:00'))], [], NOW)
    assert [s.place for s in stays] == [None]
