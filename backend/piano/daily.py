"""Deterministic daily piano routines and their persisted attempts."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from ulid import ULID

from backend.day_boundary import day_key_for
from backend.db.connection import row_to_dict


LEVELS = ('beginner', 'intermediate', 'advanced')
KEYS = ('C', 'G', 'D', 'A', 'E', 'F', 'B-flat', 'E-flat', 'A-flat')


@dataclass(frozen=True)
class Exercise:
    key: str
    title: str
    category: str
    style: str
    description: str
    instructions: str
    minutes: int
    gradeable: bool = False
    notation: str | None = None


EXERCISES = (
    Exercise('five-finger', 'Five-finger warm-up', 'Warm-up', 'shared',
             'Relaxed, even fingers in today’s key.',
             'Play slowly with loose wrists, legato up and down, hands separately then together.', 3, True, 'scale5'),
    Exercise('scales', 'Scale and arpeggio', 'Technique', 'shared',
             'Build evenness and familiarity with every key.',
             'Play two octaves up and down, then the tonic arpeggio. Accuracy comes before speed.', 6, True, 'scale'),
    Exercise('classical-cadence', 'Classical cadence', 'Harmony', 'classical',
             'Connect tonic, predominant, and dominant harmony.',
             'Play I–IV–I–V–I with smooth voice leading. Repeat softly and then with a shaped phrase.', 5, True, 'cadence'),
    Exercise('articulation', 'Articulation study', 'Technique', 'classical',
             'Coordinate touch, balance, and dynamic shape.',
             'Repeat the five-finger pattern legato, staccato, then with a crescendo and diminuendo.', 4, True, 'scale5'),
    Exercise('sight-reading', 'Sight-reading', 'Reading', 'classical',
             'Read forward without stopping.',
             'Choose an unfamiliar short passage. Scan key, meter, and patterns, then play once at a steady slow pulse.', 5),
    Exercise('ii-v-i', 'ii–V–I voice leading', 'Harmony', 'jazz',
             'Hear and connect the core jazz progression.',
             'Play shell voicings in today’s key. Keep common tones and move every voice the shortest distance.', 5, True, 'jazz'),
    Exercise('comping', 'Comping and time', 'Rhythm', 'jazz',
             'Practice space, swing feel, and chord placement.',
             'Comp over a blues or standard with the metronome on 2 and 4. Leave space between phrases.', 5),
    Exercise('guide-tone-solo', 'Guide-tone improvisation', 'Creative', 'jazz',
             'Connect melody to harmony using thirds and sevenths.',
             'Improvise over ii–V–I using only guide tones. Sing a phrase first, then find it on the piano.', 5),
    Exercise('ear-phrase', 'Learn a phrase by ear', 'Ear', 'shared',
             'Turn listening into keyboard vocabulary.',
             'Choose a short phrase from a recording, sing it, find it without notation, then transpose it once.', 5),
)
BY_KEY = {item.key: item for item in EXERCISES}


def _pick(day_key: str, values: tuple[str, ...], salt: str = '') -> str:
    digest = hashlib.sha256(f'{day_key}:{salt}'.encode()).digest()
    return values[int.from_bytes(digest[:4], 'big') % len(values)]


def preferences(db) -> dict:
    row = db.execute('SELECT * FROM piano_practice_preferences WHERE id=1').fetchone()
    if row is None:
        now = int(time.time())
        db.execute(
            'INSERT INTO piano_practice_preferences '
            '(id,session_minutes,skill_level,jazz_percent,updated_at) VALUES (1,25,\'intermediate\',50,?)',
            (now,),
        )
        db.commit()
        row = db.execute('SELECT * FROM piano_practice_preferences WHERE id=1').fetchone()
    return row_to_dict(row)


def update_preferences(db, *, session_minutes: int, skill_level: str, jazz_percent: int) -> dict:
    if session_minutes < 10 or session_minutes > 90:
        raise ValueError('sessionMinutes must be between 10 and 90.')
    if skill_level not in LEVELS:
        raise ValueError('skillLevel must be beginner, intermediate, or advanced.')
    if jazz_percent < 0 or jazz_percent > 100:
        raise ValueError('jazzPercent must be between 0 and 100.')
    db.execute(
        'INSERT INTO piano_practice_preferences '
        '(id,session_minutes,skill_level,jazz_percent,updated_at) VALUES (1,?,?,?,?) '
        'ON CONFLICT(id) DO UPDATE SET session_minutes=excluded.session_minutes, '
        'skill_level=excluded.skill_level,jazz_percent=excluded.jazz_percent,updated_at=excluded.updated_at',
        (session_minutes, skill_level, jazz_percent, int(time.time())),
    )
    db.commit()
    return preferences(db)


def _plan(pref: dict) -> list[Exercise]:
    minutes = pref['sessionMinutes']
    jazz = pref['jazzPercent']
    result = [BY_KEY['five-finger'], BY_KEY['scales']]
    if jazz <= 25:
        result += [BY_KEY['classical-cadence'], BY_KEY['articulation']]
    elif jazz >= 75:
        result += [BY_KEY['ii-v-i'], BY_KEY['guide-tone-solo']]
    else:
        result += [BY_KEY['classical-cadence'], BY_KEY['ii-v-i']]
    result.append(BY_KEY['ear-phrase'] if jazz >= 50 else BY_KEY['sight-reading'])
    if minutes >= 30:
        result.append(BY_KEY['comping'] if jazz >= 50 else BY_KEY['articulation'])
    return result


def today(db, day_key: str | None = None) -> dict:
    key = day_key or day_key_for()
    pref = preferences(db)
    rows = db.execute(
        'SELECT * FROM piano_daily_exercises WHERE day_key=? ORDER BY position', (key,)
    ).fetchall()
    if not rows:
        now = int(time.time())
        chosen_key = _pick(key, KEYS, pref['skillLevel'])
        plan = _plan(pref)
        piece = db.execute(
            'SELECT id FROM piano_pieces ORDER BY updated_at DESC LIMIT 1'
        ).fetchone()
        repertoire_minutes = max(5, pref['sessionMinutes'] // 4) if piece else 0
        allocated = _allocate_minutes(
            plan, pref['sessionMinutes'] - repertoire_minutes
        )
        for position, (exercise, minutes) in enumerate(zip(plan, allocated)):
            tempo = {'beginner': 60, 'intermediate': 80, 'advanced': 100}[pref['skillLevel']] if exercise.gradeable else None
            db.execute(
                'INSERT INTO piano_daily_exercises '
                '(id,day_key,exercise_key,position,key_name,target_tempo,minutes,created_at) '
                'VALUES (?,?,?,?,?,?,?,?)',
                (str(ULID()), key, exercise.key, position, chosen_key, tempo, minutes, now),
            )
        # Repertoire is useful only when there is something local to open.
        if piece:
            db.execute(
                'INSERT INTO piano_daily_exercises '
                '(id,day_key,exercise_key,position,key_name,minutes,piano_piece_id,measure_start,measure_end,created_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?)',
                (str(ULID()), key, 'repertoire', len(plan), None, repertoire_minutes, piece['id'], 1, 8, now),
            )
        db.commit()
        rows = db.execute(
            'SELECT * FROM piano_daily_exercises WHERE day_key=? ORDER BY position', (key,)
        ).fetchall()
    return {'dayKey': key, 'preferences': pref, 'exercises': [_serialize(db, row) for row in rows]}


def history(db, *, limit: int = 14) -> list[dict]:
    """Recent generated practice days, including incomplete days."""
    rows = db.execute(
        'SELECT day_key, COUNT(*) AS exercise_count, '
        'SUM(CASE WHEN completed_at IS NOT NULL THEN 1 ELSE 0 END) AS completed_count, '
        'SUM(minutes) AS minutes_planned '
        'FROM piano_daily_exercises GROUP BY day_key '
        'ORDER BY day_key DESC LIMIT ?',
        (limit,),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def _allocate_minutes(plan: list[Exercise], total: int) -> list[int]:
    weights = [item.minutes for item in plan]
    weight_total = sum(weights)
    values = [max(2, round(total * weight / weight_total)) for weight in weights]
    values[-1] += total - sum(values)
    return values


def _serialize(db, row) -> dict:
    data = row_to_dict(row)
    definition = BY_KEY.get(row['exercise_key'])
    if definition:
        data.update({
            'title': definition.title, 'category': definition.category,
            'style': definition.style, 'description': definition.description,
            'instructions': definition.instructions, 'gradeable': definition.gradeable,
        })
    else:
        piece = db.execute('SELECT title,composer FROM piano_pieces WHERE id=?', (row['piano_piece_id'],)).fetchone()
        data.update({
            'title': 'Repertoire focus', 'category': 'Repertoire', 'style': 'shared',
            'description': f"Work deliberately on {piece['title']}." if piece else 'Work on a saved piece.',
            'instructions': 'Practice the assigned measures slowly, isolate trouble spots, then reconnect the phrase.',
            'gradeable': True, 'pieceTitle': piece['title'] if piece else None,
            'pieceComposer': piece['composer'] if piece else None,
        })
    attempt = db.execute(
        'SELECT * FROM piano_exercise_attempts WHERE daily_exercise_id=? ORDER BY completed_at DESC LIMIT 1',
        (row['id'],),
    ).fetchone()
    data['latestAttempt'] = row_to_dict(attempt) if attempt else None
    return data


def record_attempt(db, daily_id: str, values: dict) -> dict | None:
    row = db.execute('SELECT id FROM piano_daily_exercises WHERE id=?', (daily_id,)).fetchone()
    if row is None:
        return None
    rating = values.get('selfRating')
    if rating is not None and (not isinstance(rating, int) or not 1 <= rating <= 5):
        raise ValueError('selfRating must be a whole number from 1 to 5.')
    now = int(time.time())
    attempt_id = str(ULID())
    db.execute(
        'INSERT INTO piano_exercise_attempts '
        '(id,daily_exercise_id,started_at,completed_at,tempo,correct_notes,wrong_notes,self_rating,notes,created_at) '
        'VALUES (?,?,?,?,?,?,?,?,?,?)',
        (attempt_id, daily_id, values.get('startedAt'), now, values.get('tempo'),
         values.get('correctNotes'), values.get('wrongNotes'), rating,
         (values.get('notes') or '').strip() or None, now),
    )
    db.execute(
        'UPDATE piano_daily_exercises SET completed_at=COALESCE(completed_at,?) WHERE id=?',
        (now, daily_id),
    )
    db.commit()
    return row_to_dict(db.execute('SELECT * FROM piano_exercise_attempts WHERE id=?', (attempt_id,)).fetchone())


def exercise_score(db, daily_id: str) -> bytes | None:
    row = db.execute('SELECT * FROM piano_daily_exercises WHERE id=?', (daily_id,)).fetchone()
    if row is None:
        return None
    if row['exercise_key'] == 'repertoire':
        return None
    definition = BY_KEY.get(row['exercise_key'])
    if not definition or not definition.notation:
        return None
    return _musicxml(row['key_name'] or 'C', definition.notation, definition.title).encode()


def _musicxml(key_name: str, pattern: str, title: str) -> str:
    roots = {'C': 60, 'G': 67, 'D': 62, 'A': 69, 'E': 64, 'F': 65,
             'B-flat': 58, 'E-flat': 63, 'A-flat': 56}
    root = roots[key_name]
    if pattern == 'scale5':
        offsets = [0, 2, 4, 5, 7, 5, 4, 2, 0]
    elif pattern == 'scale':
        offsets = [0, 2, 4, 5, 7, 9, 11, 12, 11, 9, 7, 5, 4, 2, 0]
    elif pattern == 'cadence':
        offsets = [[0, 4, 7], [5, 9, 12], [0, 4, 7], [7, 11, 14], [0, 4, 7]]
    else:
        # ii7 – V7 – Imaj7, deliberately root-position for Stage 1 exact-note grading.
        offsets = [[2, 5, 9, 12], [7, 11, 14, 17], [0, 4, 7, 11]]
    events = offsets
    notes = []
    for event in events:
        pitches = event if isinstance(event, list) else [event]
        for index, offset in enumerate(pitches):
            midi = root + offset
            pitch_class = midi % 12
            names = {0: ('C', 0), 1: ('C', 1), 2: ('D', 0), 3: ('E', -1), 4: ('E', 0),
                     5: ('F', 0), 6: ('F', 1), 7: ('G', 0), 8: ('A', -1), 9: ('A', 0),
                     10: ('B', -1), 11: ('B', 0)}
            step, alter = names[pitch_class]
            alter_xml = f'<alter>{alter}</alter>' if alter else ''
            chord = '<chord/>' if index else ''
            notes.append(f'<note>{chord}<pitch><step>{step}</step>{alter_xml}<octave>{midi // 12 - 1}</octave></pitch><duration>1</duration><staff>1</staff></note>')
    body = ''.join(notes)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0"><work><work-title>{title} in {key_name}</work-title></work>
<part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions><key><fifths>0</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes>{body}</measure></part></score-partwise>'''
