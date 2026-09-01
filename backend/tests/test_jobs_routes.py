"""End-to-end through HTTP: profile CRUD, tailoring, download, retention.

The model is stubbed everywhere. The point of these tests is the plumbing —
that the bullet the model picked is the bullet that reaches the PDF, and that
the PDF stops being downloadable once retention has taken it.
"""
import json
import time

import pytest

from backend.db.connection import get_db

DAY = 86400
NOW = int(time.time())


@pytest.fixture
def jobs_root(tmp_path, monkeypatch):
    monkeypatch.setenv('JOBS_ROOT', str(tmp_path / 'jobs'))
    return tmp_path / 'jobs'


@pytest.fixture
def profile(client):
    """A minimal but real profile: one role, two bullets, one skill."""
    client.patch('/api/jobs/profile', json={
        'fullName': 'Ada Lovelace', 'email': 'ada@example.com',
        'phone': '+1 416 555 0100', 'location': 'Toronto, ON',
        'headline': 'Backend engineer',
        'links': [{'label': 'GitHub', 'url': 'https://github.com/ada'}],
    })
    role_id = client.post('/api/jobs/profile/roles', json={
        'company': 'Acme', 'title': 'Engineer',
        'startLabel': '2021', 'endLabel': 'Present', 'ord': 0,
    }).get_json()['id']
    bullets = [
        client.post('/api/jobs/profile/bullets', json={
            'roleId': role_id, 'text': 'Built billing in Python.', 'ord': 0,
        }).get_json()['id'],
        client.post('/api/jobs/profile/bullets', json={
            'roleId': role_id, 'text': 'Cut page load time in half.', 'ord': 1,
        }).get_json()['id'],
    ]
    client.post('/api/jobs/profile/skills', json={'name': 'Python', 'category': 'Languages'})
    return {'roleId': role_id, 'bulletIds': bullets}


@pytest.fixture
def job(client):
    return client.post('/api/jobs', json={
        'title': 'Backend Engineer', 'company': 'Globex',
        'location': 'Remote', 'url': 'https://globex.example/jobs/1',
        'description': 'We need Python and Kubernetes experience.',
    }).get_json()


def stub_model(monkeypatch, payload):
    from backend.jobs import tailor
    monkeypatch.setattr(tailor, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(tailor, 'chat_json', lambda *a, **k: payload)


# --- profile --------------------------------------------------------------

def test_profile_round_trips(client, profile):
    loaded = client.get('/api/jobs/profile').get_json()
    assert loaded['profile']['fullName'] == 'Ada Lovelace'
    assert loaded['profile']['links'] == [{'label': 'GitHub', 'url': 'https://github.com/ada'}]
    assert len(loaded['roles']) == 1
    assert [b['text'] for b in loaded['roles'][0]['bullets']] == [
        'Built billing in Python.', 'Cut page load time in half.',
    ]
    assert loaded['skills'][0]['name'] == 'Python'


def test_screening_defaults_round_trip(client):
    body = {
        'workAuthorization': 'Canadian citizen',
        'salaryExpectation': '$140k–160k CAD',
        'noticePeriod': 'Two weeks',
        'availabilityDate': '2026-09-15',
        'relocationWillingness': 'Toronto or Montreal',
        'securityClearance': 'Reliability Status',
        'eeoAnswers': 'Prefer not to answer',
    }
    response = client.patch('/api/jobs/profile', json=body)
    assert response.status_code == 200
    profile = response.get_json()['profile']
    for key, value in body.items():
        assert profile[key] == value


def test_profile_exists_before_anything_is_written(client):
    """The migration seeds row 1, so no caller has to handle its absence."""
    assert client.get('/api/jobs/profile').get_json()['profile']['id'] == 1


def test_bullet_requires_a_role(client):
    assert client.post('/api/jobs/profile/bullets', json={'text': 'orphan'}).status_code == 400


def test_unknown_profile_section_is_404(client):
    assert client.post('/api/jobs/profile/nonsense', json={}).status_code == 404


def test_deleting_a_role_cascades_to_its_bullets(client, profile):
    client.delete(f"/api/jobs/profile/roles/{profile['roleId']}")
    assert client.get('/api/jobs/profile').get_json()['roles'] == []
    assert get_db().execute('SELECT COUNT(*) c FROM profile_bullets').fetchone()['c'] == 0


def test_updating_a_missing_item_is_404(client):
    assert client.patch('/api/jobs/profile/skills/nope', json={'name': 'x'}).status_code == 404


# --- jobs and applications ------------------------------------------------

def test_create_job_from_typed_fields(client, job):
    assert job['company'] == 'Globex'
    assert client.get(f"/api/jobs/{job['id']}").status_code == 200


def test_create_job_needs_a_title_or_company(client):
    resp = client.post('/api/jobs', json={'description': 'just a body'})
    assert resp.status_code == 400


def test_create_job_rejects_a_private_url(client):
    """The SSRF guard: this endpoint fetches a client-supplied URL."""
    resp = client.post('/api/jobs', json={'url': 'http://127.0.0.1:5000/admin'})
    assert resp.status_code == 400
    assert 'not reachable' in resp.get_json()['error']


def test_one_application_per_job(client, job):
    first = client.post('/api/jobs/applications', json={'jobId': job['id']})
    second = client.post('/api/jobs/applications', json={'jobId': job['id']})
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.get_json()['existing'] is True
    assert second.get_json()['id'] == first.get_json()['id']


def test_application_status_history_records_real_transitions_once(client, job):
    application_id = client.post(
        '/api/jobs/applications', json={'jobId': job['id']}
    ).get_json()['id']
    client.patch(f'/api/jobs/applications/{application_id}', json={'status': 'ready'})
    client.patch(f'/api/jobs/applications/{application_id}', json={'status': 'ready'})
    client.post(f'/api/jobs/applications/{application_id}/submit', json={})
    detail = client.get(f'/api/jobs/applications/{application_id}').get_json()
    assert [event['status'] for event in detail['statusEvents']] == [
        'draft', 'ready', 'submitted',
    ]
    assert [event['source'] for event in detail['statusEvents']] == [
        'created', 'manual', 'submission',
    ]


def test_application_needs_a_real_job(client):
    assert client.post('/api/jobs/applications', json={'jobId': 'nope'}).status_code == 400


def test_submit_stamps_applied_at_and_a_purge_date(client, job, jobs_root):
    application_id = client.post(
        '/api/jobs/applications', json={'jobId': job['id']}
    ).get_json()['id']
    assert client.post(f'/api/jobs/applications/{application_id}/submit',
                       json={'appliedEmail': 'ada@example.com'}).status_code == 200

    row = get_db().execute(
        'SELECT status, applied_at, applied_email, purge_after FROM applications WHERE id=?',
        (application_id,),
    ).fetchone()
    assert row['status'] == 'submitted'
    assert row['applied_at'] is not None
    assert row['applied_email'] == 'ada@example.com'
    assert row['purge_after'] == row['applied_at'] + 180 * DAY


def test_status_change_to_rejected_starts_the_short_clock(client, job, jobs_root):
    application_id = client.post(
        '/api/jobs/applications', json={'jobId': job['id']}
    ).get_json()['id']
    client.post(f'/api/jobs/applications/{application_id}/submit', json={})
    client.patch(f'/api/jobs/applications/{application_id}', json={'status': 'rejected'})

    row = get_db().execute(
        'SELECT closed_at, purge_after FROM applications WHERE id=?', (application_id,)
    ).fetchone()
    assert row['closed_at'] is not None
    assert row['purge_after'] == row['closed_at'] + 30 * DAY


def test_unknown_status_is_rejected(client, job):
    application_id = client.post(
        '/api/jobs/applications', json={'jobId': job['id']}
    ).get_json()['id']
    resp = client.patch(f'/api/jobs/applications/{application_id}', json={'status': 'hired'})
    assert resp.status_code == 400


# --- tailoring ------------------------------------------------------------

def test_tailoring_only_emits_real_bullets(client, profile, job, jobs_root, monkeypatch):
    """The anti-fabrication guarantee, end to end: index 99 does not exist."""
    application_id = client.post(
        '/api/jobs/applications', json={'jobId': job['id']}
    ).get_json()['id']
    stub_model(monkeypatch, {
        'summary': 'Backend engineer.',
        'selectedBullets': [
            {'index': 0, 'rewritten': 'Built billing in Python.'},
            {'index': 99, 'rewritten': 'Ran a division of 400 people.'},
        ],
        'emphasis': ['python'],
    })

    resp = client.post(f'/api/jobs/applications/{application_id}/tailor', json={})
    assert resp.status_code == 201
    body = resp.get_json()

    real_ids = set(profile['bulletIds'])
    assert {b['bulletId'] for b in body['content']['selectedBullets']} <= real_ids
    assert len(body['content']['selectedBullets']) == 1
    assert 'Ran a division' not in body['html']


def test_tailoring_computes_the_keyword_gap_rather_than_asking_for_it(
    client, profile, job, jobs_root, monkeypatch
):
    application_id = client.post(
        '/api/jobs/applications', json={'jobId': job['id']}
    ).get_json()['id']
    stub_model(monkeypatch, {'summary': '', 'selectedBullets': [],
                             'emphasis': ['python', 'kubernetes']})

    body = client.post(
        f'/api/jobs/applications/{application_id}/tailor', json={}
    ).get_json()

    assert body['content']['keywords']['matched'] == ['python']
    assert 'kubernetes' in body['content']['keywords']['missing']
    # The model tried to claim Kubernetes; the profile cannot back it.
    assert body['content']['emphasis'] == ['python']


def test_tailoring_stores_the_steer_and_moves_the_application_to_ready(
    client, profile, job, jobs_root, monkeypatch
):
    application_id = client.post(
        '/api/jobs/applications', json={'jobId': job['id']}
    ).get_json()['id']
    stub_model(monkeypatch, {'summary': 'x', 'selectedBullets': []})

    client.post(f'/api/jobs/applications/{application_id}/tailor',
                json={'steer': 'emphasise the payments work'})

    row = get_db().execute(
        'SELECT steer, status FROM applications WHERE id=?', (application_id,)
    ).fetchone()
    assert row['steer'] == 'emphasise the payments work'
    assert row['status'] == 'ready'


def test_tailoring_without_a_model_is_503_not_a_generic_resume(
    client, profile, job, jobs_root, monkeypatch
):
    from backend.jobs import tailor
    monkeypatch.setattr(tailor, 'is_ai_configured', lambda: False)
    application_id = client.post(
        '/api/jobs/applications', json={'jobId': job['id']}
    ).get_json()['id']

    resp = client.post(f'/api/jobs/applications/{application_id}/tailor', json={})
    assert resp.status_code == 503
    assert get_db().execute('SELECT COUNT(*) c FROM resume_versions').fetchone()['c'] == 0


def test_tailoring_an_empty_profile_is_refused(client, job, jobs_root, monkeypatch):
    stub_model(monkeypatch, {'summary': '', 'selectedBullets': []})
    application_id = client.post(
        '/api/jobs/applications', json={'jobId': job['id']}
    ).get_json()['id']
    resp = client.post(f'/api/jobs/applications/{application_id}/tailor', json={})
    assert resp.status_code == 400
    assert 'empty' in resp.get_json()['error']


# --- downloads ------------------------------------------------------------

def test_rendered_files_land_under_the_application_directory(
    client, profile, job, jobs_root, monkeypatch
):
    application_id = client.post(
        '/api/jobs/applications', json={'jobId': job['id']}
    ).get_json()['id']
    stub_model(monkeypatch, {
        'summary': 'Backend engineer.',
        'selectedBullets': [{'index': 0, 'rewritten': 'Built billing in Python.'}],
    })
    body = client.post(f'/api/jobs/applications/{application_id}/tailor', json={}).get_json()

    from backend.jobs import render
    if render.is_pdf_available():
        assert body['pdfAvailable'] is True
        pdf = jobs_root / application_id / f"{body['id']}.pdf"
        assert pdf.is_file()
        resp = client.get(f"/api/jobs/resumes/{body['id']}/download.pdf")
        assert resp.status_code == 200
        assert resp.data.startswith(b'%PDF')

    if render.is_docx_available():
        assert body['docxAvailable'] is True
        resp = client.get(f"/api/jobs/resumes/{body['id']}/download.docx")
        assert resp.status_code == 200
        assert resp.data.startswith(b'PK')


def test_download_rejects_an_unknown_extension(client, jobs_root):
    assert client.get('/api/jobs/resumes/whatever/download.exe').status_code == 404


def test_download_of_a_purged_resume_is_410(client, profile, job, jobs_root, monkeypatch):
    """Gone, and says so — not a bare 404 the user has to interpret."""
    application_id = client.post(
        '/api/jobs/applications', json={'jobId': job['id']}
    ).get_json()['id']
    stub_model(monkeypatch, {'summary': 'x', 'selectedBullets': []})
    version_id = client.post(
        f'/api/jobs/applications/{application_id}/tailor', json={}
    ).get_json()['id']

    client.post(f'/api/jobs/applications/{application_id}/submit', json={})
    db = get_db()
    db.execute('UPDATE applications SET applied_at=? WHERE id=?',
               (NOW - 200 * DAY, application_id))
    db.commit()

    assert client.post('/api/jobs/retention/sweep').get_json()['applications'] == 1

    resp = client.get(f'/api/jobs/resumes/{version_id}/download.pdf')
    assert resp.status_code == 410

    # The record survives the file.
    row = client.get(f'/api/jobs/resumes/{version_id}').get_json()
    assert row['purgedAt'] is not None
    assert row['html']


def test_deleting_an_application_removes_its_files(
    client, profile, job, jobs_root, monkeypatch
):
    application_id = client.post(
        '/api/jobs/applications', json={'jobId': job['id']}
    ).get_json()['id']
    stub_model(monkeypatch, {'summary': 'x', 'selectedBullets': []})
    client.post(f'/api/jobs/applications/{application_id}/tailor', json={})
    assert (jobs_root / application_id).exists()

    client.delete(f'/api/jobs/applications/{application_id}')
    assert not (jobs_root / application_id).exists()


def test_deleting_a_job_cleans_up_its_applications_files(
    client, profile, job, jobs_root, monkeypatch
):
    application_id = client.post(
        '/api/jobs/applications', json={'jobId': job['id']}
    ).get_json()['id']
    stub_model(monkeypatch, {'summary': 'x', 'selectedBullets': []})
    client.post(f'/api/jobs/applications/{application_id}/tailor', json={})

    client.delete(f"/api/jobs/{job['id']}")
    assert not (jobs_root / application_id).exists()
    assert get_db().execute('SELECT COUNT(*) c FROM applications').fetchone()['c'] == 0


# --- answer kit -----------------------------------------------------------

def test_answer_kit_resolves_from_the_profile(client, profile, job, jobs_root, monkeypatch):
    from backend.jobs import answers
    monkeypatch.setattr(answers, 'is_ai_configured', lambda: False)

    application_id = client.post(
        '/api/jobs/applications', json={'jobId': job['id']}
    ).get_json()['id']
    resp = client.post(f'/api/jobs/applications/{application_id}/answers', json={
        'questions': [
            'Email address',
            'Full name',
            'Phone number',
            'GitHub URL',
            {'label': 'Why us?', 'type': 'textarea'},
        ],
    })
    assert resp.status_code == 200
    result = resp.get_json()['answers']
    # Against the real load_profile output, whose keys are camelCased by
    # row_to_dict — the mapping has to survive that, not just a fixture dict.
    assert [r['answer'] for r in result[:4]] == [
        'ada@example.com', 'Ada Lovelace', '+1 416 555 0100',
        'https://github.com/ada',
    ]
    assert {r['source'] for r in result[:4]} == {'profile'}
    assert result[4]['source'] == 'unanswered'


def test_answer_kit_needs_questions(client, job, jobs_root):
    application_id = client.post(
        '/api/jobs/applications', json={'jobId': job['id']}
    ).get_json()['id']
    assert client.post(f'/api/jobs/applications/{application_id}/answers',
                       json={'questions': []}).status_code == 400


# --- stats ----------------------------------------------------------------

def test_stats_counts_applications(client, job, jobs_root):
    application_id = client.post(
        '/api/jobs/applications', json={'jobId': job['id']}
    ).get_json()['id']
    client.post(f'/api/jobs/applications/{application_id}/submit', json={})

    stats = client.get('/api/jobs/stats').get_json()
    assert stats['total'] == 1
    assert stats['counts']['submitted'] == 1
    assert stats['counts']['rejected'] == 0
    assert len(stats['active']) == 1
    assert stats['active'][0]['company'] == 'Globex'


def test_settings_expose_the_retention_policy(client):
    settings = client.get('/api/settings').get_json()
    assert settings['jobRetentionDays'] == 180
    assert settings['jobPurgeOnRejection'] is True
    assert settings['jobRejectionGraceDays'] == 30

    client.patch('/api/settings/ai', json={'jobRetentionDays': 90})
    assert client.get('/api/settings').get_json()['jobRetentionDays'] == 90


def test_settings_retention_change_is_honoured_by_the_sweep(client, job, jobs_root):
    from backend.db.connection import get_db as db_
    application_id = client.post(
        '/api/jobs/applications', json={'jobId': job['id']}
    ).get_json()['id']
    client.post(f'/api/jobs/applications/{application_id}/submit', json={})

    client.patch('/api/settings/ai', json={'jobRetentionDays': 10})
    db = db_()
    db.execute('UPDATE applications SET applied_at=? WHERE id=?',
               (NOW - 20 * DAY, application_id))
    db.commit()

    assert client.post('/api/jobs/retention/sweep').get_json()['applications'] == 1


def test_the_commute_radius_round_trips(client):
    response = client.patch('/api/jobs/profile', json={'maxDistanceKm': 200})
    assert response.status_code == 200
    assert response.get_json()['profile']['maxDistanceKm'] == 200


def test_the_commute_radius_distinguishes_unset_from_zero(client):
    """The reason it is handled outside `field_map`.

    That loop writes `body[camel] or ''`, which would store the empty string
    for both — and on a REAL column "no radius" and "0 km" mean opposite
    things: one syncs everything, the other would empty the feed.
    """
    client.patch('/api/jobs/profile', json={'maxDistanceKm': 200})
    zeroed = client.patch('/api/jobs/profile', json={'maxDistanceKm': 0})
    assert zeroed.get_json()['profile']['maxDistanceKm'] == 0

    cleared = client.patch('/api/jobs/profile', json={'maxDistanceKm': None})
    assert cleared.get_json()['profile']['maxDistanceKm'] is None


def test_the_radius_defaults_to_unset(client):
    """Shipping a default would silently filter a feed nobody asked to filter."""
    assert client.get('/api/jobs/profile').get_json()['profile']['maxDistanceKm'] is None


def test_a_nonsense_radius_clears_it_rather_than_failing_the_save(client):
    response = client.patch('/api/jobs/profile', json={'maxDistanceKm': 'soon'})
    assert response.status_code == 200
    assert response.get_json()['profile']['maxDistanceKm'] is None
