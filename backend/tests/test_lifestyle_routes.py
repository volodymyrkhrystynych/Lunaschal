"""Lifestyle tab routes: workouts, heatmap, progression, weight, selfies, calories."""
import io

import pytest
from PIL import Image

from backend.routes import lifestyle


@pytest.fixture(autouse=True)
def lifestyle_root(monkeypatch, tmp_path):
    root = tmp_path / 'lifestyle'
    monkeypatch.setenv('LIFESTYLE_ROOT', str(root))
    return root


@pytest.fixture(autouse=True)
def sync_bg(monkeypatch):
    """Run the background parse inline so create -> structured sets is deterministic."""
    monkeypatch.setattr(lifestyle, 'run_bg', lambda fn: fn())


@pytest.fixture(autouse=True)
def stub_parser(monkeypatch):
    """Stand in for the LLM. Understands the doc's `name w,r w,r` shorthand — and
    the bodyweight form `name r r r` (bare rep counts, no weight) — so route
    tests exercise real parsed rows without touching a model."""
    def fake(text):
        exercises = []
        for line in text.splitlines():
            parts = line.split()
            name_words, sets = [], []
            for p in parts:
                if ',' in p:
                    weight, reps = p.split(',', 1)
                    sets.append({'weight': float(weight), 'reps': int(reps)})
                elif p.isdigit():
                    sets.append({'weight': None, 'reps': int(p)})
                else:
                    name_words.append(p)
            if name_words:
                exercises.append({'name': ' '.join(name_words), 'sets': sets})
        return exercises

    monkeypatch.setattr(lifestyle, 'parse_workout', fake)
    return fake


def _png_bytes(color=(120, 120, 120)):
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), color).save(buf, 'PNG')
    return buf.getvalue()


def _create_workout(client, **fields):
    body = {'locationType': 'outside', **fields}
    return client.post('/api/lifestyle/workouts', json=body)


# --- Workout sessions ---

def test_create_workout_parses_freeform_text_into_sets(client):
    res = _create_workout(
        client,
        date='2026-07-20',
        locationType='goodlife_brother',
        durationMinutes=65,
        intensityRating=4,
        rawText='bicep curls 20,10 20,10\nsquats 60,8 60,8 65,6',
    )
    assert res.status_code == 201
    body = res.get_json()
    assert body['locationType'] == 'goodlife_brother'
    assert body['durationMinutes'] == 65
    assert body['intensityRating'] == 4

    detail = client.get(f'/api/lifestyle/workouts/{body["id"]}').get_json()
    assert detail['parseStatus'] == 'done'
    names = [e['nameCanonical'] for e in detail['exercises']]
    assert names == ['bicep curl', 'squat']
    curls, squats = detail['exercises']
    assert [(s['weight'], s['reps']) for s in curls['sets']] == [(20.0, 10), (20.0, 10)]
    assert [(s['weight'], s['reps']) for s in squats['sets']] == [
        (60.0, 8), (60.0, 8), (65.0, 6)
    ]


def test_raw_text_survives_a_failed_parse(client, monkeypatch):
    monkeypatch.setattr(lifestyle, 'parse_workout', lambda text: None)
    res = _create_workout(client, rawText='bicep curls 20,10')
    detail = client.get(f'/api/lifestyle/workouts/{res.get_json()["id"]}').get_json()
    assert detail['parseStatus'] == 'error'
    assert detail['rawText'] == 'bicep curls 20,10'
    assert detail['exercises'] == []


def test_reparse_reruns_the_parse_over_untouched_raw_text(client, monkeypatch):
    monkeypatch.setattr(lifestyle, 'parse_workout', lambda text: None)
    session_id = _create_workout(client, rawText='bicep curls 20,10').get_json()['id']

    monkeypatch.setattr(
        lifestyle, 'parse_workout',
        lambda text: [{'name': 'bicep curls', 'sets': [{'weight': 20.0, 'reps': 10}]}],
    )
    assert client.post(f'/api/lifestyle/workouts/{session_id}/reparse').status_code == 200
    detail = client.get(f'/api/lifestyle/workouts/{session_id}').get_json()
    assert detail['parseStatus'] == 'done'
    assert detail['exercises'][0]['nameCanonical'] == 'bicep curl'


def test_reparse_without_raw_text_is_rejected(client):
    session_id = _create_workout(client).get_json()['id']
    res = client.post(f'/api/lifestyle/workouts/{session_id}/reparse')
    assert res.status_code == 400


def test_session_without_text_is_marked_skipped_not_pending(client):
    res = _create_workout(client, durationMinutes=30)
    assert res.get_json()['parseStatus'] == 'skipped'


def test_editing_raw_text_replaces_the_parsed_sets(client):
    session_id = _create_workout(client, rawText='bicep curls 20,10').get_json()['id']
    res = client.patch(
        f'/api/lifestyle/workouts/{session_id}', json={'rawText': 'squats 60,8'}
    )
    assert res.status_code == 200
    detail = client.get(f'/api/lifestyle/workouts/{session_id}').get_json()
    assert [e['nameCanonical'] for e in detail['exercises']] == ['squat']


def test_clearing_raw_text_drops_the_parsed_sets(client):
    session_id = _create_workout(client, rawText='bicep curls 20,10').get_json()['id']
    client.patch(f'/api/lifestyle/workouts/{session_id}', json={'rawText': ''})
    detail = client.get(f'/api/lifestyle/workouts/{session_id}').get_json()
    assert detail['exercises'] == []
    assert detail['parseStatus'] == 'skipped'


@pytest.mark.parametrize('body,field', [
    ({'locationType': 'gym'}, 'locationType'),
    ({'locationType': 'outside', 'date': '20-07-2026'}, 'date'),
    ({'locationType': 'outside', 'date': '2026-02-30'}, 'date'),
    ({'locationType': 'outside', 'intensityRating': 6}, 'intensityRating'),
    ({'locationType': 'outside', 'intensityRating': 0}, 'intensityRating'),
    ({'locationType': 'outside', 'durationMinutes': -5}, 'durationMinutes'),
])
def test_create_workout_rejects_bad_input(client, body, field):
    res = client.post('/api/lifestyle/workouts', json=body)
    assert res.status_code == 400
    assert field in res.get_json()['error']


def test_intensity_accepts_the_whole_five_star_range(client):
    for stars in (1, 2, 3, 4, 5):
        res = _create_workout(client, intensityRating=stars)
        assert res.status_code == 201
        assert res.get_json()['intensityRating'] == stars


def test_patching_intensity_past_five_stars_is_rejected(client):
    session_id = _create_workout(client, intensityRating=3).get_json()['id']
    res = client.patch(
        f'/api/lifestyle/workouts/{session_id}', json={'intensityRating': 7}
    )
    assert res.status_code == 400
    assert 'intensityRating' in res.get_json()['error']
    detail = client.get(f'/api/lifestyle/workouts/{session_id}').get_json()
    assert detail['intensityRating'] == 3


def test_bodyweight_reps_are_stored_with_a_null_weight(client):
    """"squats 10 10 10 10" — four sets of ten at bodyweight. A weight of 0
    would be a lie the progression chart would then plot."""
    session_id = _create_workout(
        client, date='2026-07-20', rawText='squats 10 10 10 10'
    ).get_json()['id']
    detail = client.get(f'/api/lifestyle/workouts/{session_id}').get_json()
    sets = detail['exercises'][0]['sets']
    assert [(s['weight'], s['reps']) for s in sets] == [(None, 10)] * 4


def test_progression_for_a_bodyweight_exercise_reports_reps_not_zero_weight(client):
    _create_workout(client, date='2026-07-01', rawText='squats 10 10 10')
    _create_workout(client, date='2026-07-08', rawText='squats 12 12 12 12')
    res = client.get('/api/lifestyle/exercises/squat/progression').get_json()
    assert [p['maxWeight'] for p in res['points']] == [None, None]
    assert [p['totalVolume'] for p in res['points']] == [None, None]
    assert [p['totalReps'] for p in res['points']] == [30, 48]
    assert [p['setCount'] for p in res['points']] == [3, 4]


def test_delete_workout_cascades_to_exercises_and_sets(client):
    session_id = _create_workout(client, rawText='bicep curls 20,10').get_json()['id']
    assert client.delete(f'/api/lifestyle/workouts/{session_id}').status_code == 200
    assert client.get(f'/api/lifestyle/workouts/{session_id}').status_code == 404
    assert client.get('/api/lifestyle/exercises').get_json() == []


def test_workouts_list_is_newest_day_first(client):
    _create_workout(client, date='2026-07-01')
    _create_workout(client, date='2026-07-20')
    dates = [w['date'] for w in client.get('/api/lifestyle/workouts').get_json()]
    assert dates == ['2026-07-20', '2026-07-01']


# --- Activity heatmap ---

def test_heatmap_takes_the_highest_priority_activity_and_flags_a_secondary(client):
    _create_workout(client, date='2026-07-20', locationType='outside', durationMinutes=30,
                    intensityRating=2)
    _create_workout(client, date='2026-07-20', locationType='goodlife_brother',
                    durationMinutes=60, intensityRating=5)
    day = client.get('/api/lifestyle/heatmap').get_json()[0]
    assert day['date'] == '2026-07-20'
    assert day['activityType'] == 'goodlife_brother'
    assert day['secondary'] is True
    assert day['durationMinutes'] == 90        # summed across the day
    assert day['intensityRating'] == 5         # the day's hardest session
    assert len(day['sessions']) == 2


def test_lifting_at_home_is_a_loggable_activity(client):
    res = _create_workout(client, date='2026-07-21', locationType='lifting_home',
                          rawText='bench 60,8 60,8')
    assert res.status_code == 201
    day = client.get('/api/lifestyle/heatmap').get_json()[0]
    assert day['activityType'] == 'lifting_home'


def test_heatmap_does_not_flag_a_secondary_for_two_sessions_of_one_type(client):
    _create_workout(client, date='2026-07-20', locationType='outside')
    _create_workout(client, date='2026-07-20', locationType='outside')
    day = client.get('/api/lifestyle/heatmap').get_json()[0]
    assert day['secondary'] is False


def test_heatmap_respects_the_date_range_and_omits_empty_days(client):
    _create_workout(client, date='2026-07-01')
    _create_workout(client, date='2026-07-20')
    days = client.get(
        '/api/lifestyle/heatmap?start=2026-07-10&end=2026-07-31'
    ).get_json()
    assert [d['date'] for d in days] == ['2026-07-20']


def test_heatmap_rejects_a_malformed_range(client):
    assert client.get('/api/lifestyle/heatmap?start=july').status_code == 400


# --- Exercises & progression ---

def test_variant_spellings_collapse_into_one_exercise_series(client):
    _create_workout(client, date='2026-07-01', rawText='bicep curls 20,10')
    _create_workout(client, date='2026-07-08', rawText='Bicep Curl 22,10')
    _create_workout(client, date='2026-07-15', rawText='curls 25,8')

    exercises = client.get('/api/lifestyle/exercises').get_json()
    assert len(exercises) == 1
    assert exercises[0]['name'] == 'bicep curl'
    assert exercises[0]['displayName'] == 'Bicep Curl'
    assert exercises[0]['sessionCount'] == 3
    assert exercises[0]['lastDate'] == '2026-07-15'


def test_progression_reports_both_top_weight_and_volume_per_day(client):
    _create_workout(client, date='2026-07-01', rawText='squats 60,8 65,6')
    _create_workout(client, date='2026-07-08', rawText='squats 70,5')
    res = client.get('/api/lifestyle/exercises/squat/progression').get_json()
    assert res['displayName'] == 'Squat'
    assert [p['date'] for p in res['points']] == ['2026-07-01', '2026-07-08']
    assert res['points'][0]['maxWeight'] == 65.0
    assert res['points'][0]['totalVolume'] == 60 * 8 + 65 * 6
    assert res['points'][0]['setCount'] == 2
    assert res['points'][1]['maxWeight'] == 70.0


def test_progression_for_an_unknown_exercise_is_empty_not_an_error(client):
    res = client.get('/api/lifestyle/exercises/nothing/progression')
    assert res.status_code == 200
    assert res.get_json()['points'] == []


def test_merge_folds_one_exercise_into_another(client):
    _create_workout(client, date='2026-07-01', rawText='squats 60,8')
    _create_workout(client, date='2026-07-08', rawText='hack squat machine 40,10')
    assert len(client.get('/api/lifestyle/exercises').get_json()) == 2

    res = client.post(
        '/api/lifestyle/exercises/merge',
        json={'from': 'hack squat machine', 'into': 'squat'},
    )
    assert res.status_code == 200
    assert res.get_json()['moved'] == 1
    names = [e['name'] for e in client.get('/api/lifestyle/exercises').get_json()]
    assert names == ['squat']


def test_merge_rejects_unknown_or_identical_names(client):
    assert client.post(
        '/api/lifestyle/exercises/merge', json={'from': 'nope', 'into': 'squat'}
    ).status_code == 404
    assert client.post(
        '/api/lifestyle/exercises/merge', json={'from': 'squat', 'into': 'squat'}
    ).status_code == 400


# --- Body weight ---

def test_weight_logs_are_returned_oldest_first_within_a_range(client):
    client.post('/api/lifestyle/weight', json={'date': '2026-07-20', 'weight': 81.2})
    client.post('/api/lifestyle/weight', json={'date': '2026-07-01', 'weight': 82.5})
    logs = client.get('/api/lifestyle/weight?start=2026-07-01').get_json()
    assert [(l['date'], l['weight']) for l in logs] == [
        ('2026-07-01', 82.5), ('2026-07-20', 81.2)
    ]


def test_relogging_a_day_corrects_it_instead_of_adding_a_point(client):
    client.post('/api/lifestyle/weight', json={'date': '2026-07-20', 'weight': 81.2})
    res = client.post('/api/lifestyle/weight', json={'date': '2026-07-20', 'weight': 80.9})
    assert res.status_code == 201
    logs = client.get('/api/lifestyle/weight').get_json()
    assert len(logs) == 1
    assert logs[0]['weight'] == 80.9


def test_weight_rejects_missing_and_out_of_range_values(client):
    assert client.post('/api/lifestyle/weight', json={}).status_code == 400
    assert client.post('/api/lifestyle/weight', json={'weight': 0}).status_code == 400
    assert client.post('/api/lifestyle/weight', json={'weight': 'heavy'}).status_code == 400


def test_delete_weight_log(client):
    log = client.post('/api/lifestyle/weight', json={'weight': 81.0}).get_json()
    assert client.delete(f'/api/lifestyle/weight/{log["id"]}').status_code == 200
    assert client.get('/api/lifestyle/weight').get_json() == []
    assert client.delete(f'/api/lifestyle/weight/{log["id"]}').status_code == 404


# --- Daily selfie ---

def _upload_selfie(client, date=None, data=None, filename='selfie.png',
                   content_type='image/png'):
    form = {'image': (io.BytesIO(data if data is not None else _png_bytes()), filename)}
    if date:
        form['date'] = date
    return client.post(
        '/api/lifestyle/selfies', data=form, content_type='multipart/form-data'
    )


def test_selfie_upload_stores_the_file_and_serves_it_back(client, lifestyle_root):
    res = _upload_selfie(client, date='2026-07-20')
    assert res.status_code == 201
    body = res.get_json()
    assert body['date'] == '2026-07-20'
    assert 'path' not in body  # the filesystem path stays server-side

    image = client.get(body['url'])
    assert image.status_code == 200
    assert image.data == _png_bytes()
    assert (lifestyle_root / body['id'] / 'selfie.png').is_file()


def test_reuploading_a_day_replaces_the_selfie_and_its_file(client, lifestyle_root):
    first = _upload_selfie(client, date='2026-07-20').get_json()
    second = _upload_selfie(
        client, date='2026-07-20', data=_png_bytes((10, 20, 30))
    ).get_json()

    selfies = client.get('/api/lifestyle/selfies').get_json()
    assert [s['id'] for s in selfies] == [second['id']]
    assert not (lifestyle_root / first['id']).exists()
    assert client.get(first['url']).status_code == 404


def test_selfies_are_listed_newest_day_first(client):
    _upload_selfie(client, date='2026-07-01')
    _upload_selfie(client, date='2026-07-20')
    dates = [s['date'] for s in client.get('/api/lifestyle/selfies').get_json()]
    assert dates == ['2026-07-20', '2026-07-01']


def test_selfie_upload_rejects_a_non_image(client):
    res = _upload_selfie(client, filename='notes.txt', content_type='text/plain')
    assert res.status_code == 400


def test_selfie_upload_rejects_an_oversized_image(client, monkeypatch):
    monkeypatch.setattr(lifestyle, 'MAX_SELFIE_BYTES', 10)
    assert _upload_selfie(client).status_code == 413


def test_delete_selfie_removes_the_row_and_the_file(client, lifestyle_root):
    selfie = _upload_selfie(client).get_json()
    assert client.delete(f'/api/lifestyle/selfies/{selfie["id"]}').status_code == 200
    assert not (lifestyle_root / selfie['id']).exists()
    assert client.get('/api/lifestyle/selfies').get_json() == []


# --- Calories ---

def test_calories_day_view_carries_a_running_total(client):
    client.post('/api/lifestyle/calories',
                json={'date': '2026-07-20', 'description': 'chicken and rice', 'calories': 600})
    client.post('/api/lifestyle/calories',
                json={'date': '2026-07-20', 'description': 'protein shake', 'calories': 180})
    client.post('/api/lifestyle/calories',
                json={'date': '2026-07-19', 'description': 'yesterday', 'calories': 999})

    day = client.get('/api/lifestyle/calories?date=2026-07-20').get_json()
    assert day['total'] == 780
    assert [e['description'] for e in day['entries']] == ['chicken and rice', 'protein shake']


def test_empty_day_totals_zero(client):
    day = client.get('/api/lifestyle/calories?date=2026-07-20').get_json()
    assert day == {'date': '2026-07-20', 'entries': [], 'total': 0}


@pytest.mark.parametrize('body', [
    {'description': '', 'calories': 600},
    {'description': 'rice'},
    {'description': 'rice', 'calories': -1},
    {'description': 'rice', 'calories': 'lots'},
])
def test_calorie_entry_rejects_bad_input(client, body):
    assert client.post('/api/lifestyle/calories', json=body).status_code == 400


def test_delete_calorie_entry(client):
    entry = client.post(
        '/api/lifestyle/calories', json={'description': 'rice', 'calories': 600}
    ).get_json()
    assert client.delete(f'/api/lifestyle/calories/{entry["id"]}').status_code == 200
    assert client.delete(f'/api/lifestyle/calories/{entry["id"]}').status_code == 404
