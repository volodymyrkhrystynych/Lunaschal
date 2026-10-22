"""Home and Work as dated roles over saved places.

A saved place is a named point; a role says that place *was* Home (or a Work)
over a range of 4am days. Keeping the two apart is what gives a move a
history: moving is a new place plus a new Home period, so last year's trail
still matches last year's flat. Editing a place's coordinates, by contrast, is
a correction and applies to every period it has ever had.

Periods are half-open day ranges [started_on, ended_on); ended_on NULL means
current. There is at most one Home on any day. Several Works may run at once,
but each is a different place, and one place never holds two roles at once —
a flat that was both would make every visit to it ambiguous.
"""
import time
from dataclasses import dataclass
from datetime import date

from ulid import ULID

from backend.day_boundary import day_key_for

ROLES = ('home', 'work')


class PlaceRoleError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RolePlace:
    """A place as it stands in a role on some day: what matching needs."""
    role: str
    place_id: str
    name: str
    lat: float
    lon: float
    radius_m: int


_SELECT = ('SELECT r.id, r.role, r.place_id, r.started_on, r.ended_on, r.created_at, r.updated_at,'
           ' p.name, p.latitude, p.longitude, p.radius_m'
           ' FROM place_roles r JOIN saved_places p ON p.id = r.place_id')


def list_periods(db) -> list[dict]:
    """Every period, newest start first."""
    return [dict(r) for r in db.execute(_SELECT + ' ORDER BY r.started_on DESC, r.created_at DESC')]


def _get(db, period_id: str) -> dict | None:
    row = db.execute(_SELECT + ' WHERE r.id=?', (period_id,)).fetchone()
    return dict(row) if row else None


def _day(value, field: str) -> str:
    if not isinstance(value, str):
        raise PlaceRoleError(f'{field} must be a YYYY-MM-DD date')
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise PlaceRoleError(f'{field} must be a YYYY-MM-DD date') from None


def _overlaps(a_start: str, a_end: str | None, b_start: str, b_end: str | None) -> bool:
    return (b_end is None or a_start < b_end) and (a_end is None or b_start < a_end)


def active_on(period: dict, day: str) -> bool:
    return period['started_on'] <= day and (period['ended_on'] is None or day < period['ended_on'])


def roles_for_day(periods: list[dict], day: str) -> list[RolePlace]:
    """The role places in force on `day`, from an already-loaded period list —
    so a caller walking many days loads the periods once."""
    return [RolePlace(p['role'], p['place_id'], p['name'], p['latitude'], p['longitude'], p['radius_m'])
            for p in periods
            if active_on(p, day) and p['latitude'] is not None and p['longitude'] is not None]


def roles_on(db, day: str) -> dict:
    """{'home': RolePlace | None, 'work': [RolePlace, ...]} for one day."""
    places = roles_for_day(list_periods(db), day)
    return {'home': next((p for p in places if p.role == 'home'), None),
            'work': [p for p in places if p.role == 'work']}


def current_home(db) -> RolePlace | None:
    return roles_on(db, day_key_for())['home']


def _check(db, role: str, place_id: str, start: str, end: str | None, *, ignore: str | None = None) -> None:
    if end is not None and end <= start:
        raise PlaceRoleError('The end date must be after the start date')
    for other in list_periods(db):
        if other['id'] == ignore or not _overlaps(start, end, other['started_on'], other['ended_on']):
            continue
        if other['place_id'] == place_id:
            if other['role'] == role:
                raise PlaceRoleError(f"{other['name']} is already {role.capitalize()} for part of that range")
            raise PlaceRoleError(f"{other['name']} is {other['role'].capitalize()} for part of that range;"
                                 ' a place holds one role at a time')
        if role == 'home' and other['role'] == 'home':
            raise PlaceRoleError(f"{other['name']} is Home for part of that range")


def assign(db, role: str, place_id: str, started_on: str | None = None) -> dict:
    """Start a period. A new Home closes the Home that was open when it starts,
    which is what "I moved on the 3rd" means; any other overlap is refused."""
    if role not in ROLES:
        raise PlaceRoleError('Role must be home or work')
    start = _day(started_on, 'startedOn') if started_on is not None else day_key_for()
    place = db.execute('SELECT latitude, longitude FROM saved_places WHERE id=?', (place_id,)).fetchone()
    if place is None:
        raise PlaceRoleError('Place not found', 404)
    if place['latitude'] is None or place['longitude'] is None:
        raise PlaceRoleError('Give the place coordinates before making it Home or Work')
    now = int(time.time())
    if role == 'home':
        # Only an open Home that began strictly earlier is the one being moved
        # out of; a same-day or later one is a conflict, not a move.
        for p in list_periods(db):
            if p['role'] == 'home' and p['ended_on'] is None and p['started_on'] < start \
                    and p['place_id'] != place_id:
                db.execute('UPDATE place_roles SET ended_on=?, updated_at=? WHERE id=?', (start, now, p['id']))
    try:
        _check(db, role, place_id, start, None)
    except PlaceRoleError:
        db.rollback()
        raise
    period_id = str(ULID())
    db.execute('INSERT INTO place_roles (id, role, place_id, started_on, ended_on, created_at, updated_at)'
               ' VALUES (?,?,?,?,NULL,?,?)', (period_id, role, place_id, start, now, now))
    db.commit()
    return _get(db, period_id)


_UNSET = object()


def update_period(db, period_id: str, *, started_on=_UNSET, ended_on=_UNSET) -> dict:
    """Correct a period's dates. No auto-closing here: an edit that would
    overlap another period is refused so the user decides which one is wrong."""
    period = _get(db, period_id)
    if period is None:
        raise PlaceRoleError('Period not found', 404)
    start = period['started_on'] if started_on is _UNSET else _day(started_on, 'startedOn')
    end = period['ended_on'] if ended_on is _UNSET else (
        None if ended_on is None else _day(ended_on, 'endedOn'))
    _check(db, period['role'], period['place_id'], start, end, ignore=period_id)
    db.execute('UPDATE place_roles SET started_on=?, ended_on=?, updated_at=? WHERE id=?',
               (start, end, int(time.time()), period_id))
    db.commit()
    return _get(db, period_id)


def delete_period(db, period_id: str) -> dict | None:
    """Remove a period; returns what it was, or None if it did not exist."""
    period = _get(db, period_id)
    if period is not None:
        db.execute('DELETE FROM place_roles WHERE id=?', (period_id,))
        db.commit()
    return period
