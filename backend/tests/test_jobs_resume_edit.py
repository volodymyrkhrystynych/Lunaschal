"""Fixing a tailored resume by hand, and the name it goes out under.

The edit route is the one place in this feature where the *user* overrules the
model's output, so the property that matters is the inverse of tailoring's:
their wording must survive intact, while the structure it hangs on must not be
editable at all.
"""
import json

import pytest

from backend.db.connection import get_db
from backend.jobs import render, tailor


@pytest.fixture
def jobs_root(tmp_path, monkeypatch):
    monkeypatch.setenv('JOBS_ROOT', str(tmp_path / 'jobs'))
    return tmp_path / 'jobs'


@pytest.fixture
def profile(client):
    client.patch('/api/jobs/profile', json={
        'fullName': 'Ada Lovelace', 'email': 'ada@example.com',
    })
    role_id = client.post('/api/jobs/profile/roles', json={
        'company': 'Acme', 'title': 'Engineer', 'ord': 0,
    }).get_json()['id']
    bullet_id = client.post('/api/jobs/profile/bullets', json={
        'roleId': role_id, 'text': 'Built billing in Python.', 'ord': 0,
    }).get_json()['id']
    client.post('/api/jobs/profile/skills', json={'name': 'Python'})
    return {'roleId': role_id, 'bulletId': bullet_id}


@pytest.fixture
def version(client, profile, jobs_root, monkeypatch):
    """One tailored resume, built through the real route with a stubbed model."""
    monkeypatch.setattr(tailor, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(tailor, 'chat_json', lambda *a, **k: {
        'summary': 'A backend engineer.',
        'selectedBullets': [{'index': 0, 'rewritten': ''}],
        'emphasis': [],
    })
    job_id = client.post('/api/jobs', json={
        'title': 'Backend Engineer', 'company': 'Globex',
        'description': 'We need Python.',
    }).get_json()['id']
    application_id = client.post(
        '/api/jobs/applications', json={'jobId': job_id}
    ).get_json()['id']
    built = client.post(
        f'/api/jobs/applications/{application_id}/tailor', json={}
    ).get_json()
    return {'applicationId': application_id, 'versionId': built['id'],
            'content': built['content']}


def bullet_patch(content, text):
    return {'bullets': [
        {'bulletId': b['bulletId'], 'text': text}
        for b in content['selectedBullets']
    ]}


# --------------------------------------------------------------------------
# apply_edits — pure
# --------------------------------------------------------------------------

BASE = {
    'summary': 'Original summary.',
    'selectedBullets': [
        {'bulletId': 'b1', 'roleId': 'r1', 'index': 0, 'company': 'Acme',
         'roleTitle': 'Engineer', 'original': 'Built billing.',
         'text': 'Built billing.', 'rewritten': False},
        {'bulletId': 'b2', 'roleId': 'r1', 'index': 1, 'company': 'Acme',
         'roleTitle': 'Engineer', 'original': 'Cut load time.',
         'text': 'Cut load time.', 'rewritten': False},
    ],
    'emphasis': ['python'],
    'keywords': {'matched': ['python'], 'missing': []},
}


def test_an_edit_replaces_the_text_and_marks_it_rewritten():
    result = tailor.apply_edits(BASE, {'bullets': [
        {'bulletId': 'b1', 'text': 'Rebuilt billing end to end.'},
    ]})
    assert result['selectedBullets'][0]['text'] == 'Rebuilt billing end to end.'
    assert result['selectedBullets'][0]['rewritten'] is True


def test_the_original_is_never_overwritten():
    """The diff the UI shows has to keep a truthful "before"."""
    result = tailor.apply_edits(BASE, {'bullets': [
        {'bulletId': 'b1', 'text': 'Something else.', 'original': 'A lie.'},
    ]})
    assert result['selectedBullets'][0]['original'] == 'Built billing.'


def test_structure_cannot_be_re_attributed():
    """A user may reword their own accomplishment; they may not move it to a
    company they never worked at."""
    result = tailor.apply_edits(BASE, {'bullets': [
        {'bulletId': 'b1', 'text': 'Fine.', 'company': 'Google',
         'roleTitle': 'VP', 'roleId': 'fake'},
    ]})
    bullet = result['selectedBullets'][0]
    assert bullet['company'] == 'Acme'
    assert bullet['roleTitle'] == 'Engineer'
    assert bullet['roleId'] == 'r1'


def test_an_omitted_bullet_is_dropped():
    result = tailor.apply_edits(BASE, {'bullets': [{'bulletId': 'b2', 'text': 'Kept.'}]})
    assert [b['bulletId'] for b in result['selectedBullets']] == ['b2']


def test_the_patch_order_becomes_the_new_order():
    result = tailor.apply_edits(BASE, {'bullets': [
        {'bulletId': 'b2', 'text': 'Second first.'},
        {'bulletId': 'b1', 'text': 'First second.'},
    ]})
    assert [b['bulletId'] for b in result['selectedBullets']] == ['b2', 'b1']


def test_an_unknown_bullet_id_is_ignored():
    """Otherwise the edit route is a way to add experience that is not in the
    profile at all — the one thing tailoring exists to prevent."""
    result = tailor.apply_edits(BASE, {'bullets': [
        {'bulletId': 'b1', 'text': 'Real.'},
        {'bulletId': 'invented', 'text': 'Led the Apollo programme.'},
    ]})
    assert [b['bulletId'] for b in result['selectedBullets']] == ['b1']


def test_a_duplicated_bullet_id_appears_once():
    result = tailor.apply_edits(BASE, {'bullets': [
        {'bulletId': 'b1', 'text': 'One.'},
        {'bulletId': 'b1', 'text': 'Two.'},
    ]})
    assert len(result['selectedBullets']) == 1
    assert result['selectedBullets'][0]['text'] == 'One.'


def test_blanking_a_bullet_restores_the_original():
    """An empty line on a resume is worse than the sentence it replaced."""
    result = tailor.apply_edits(BASE, {'bullets': [{'bulletId': 'b1', 'text': '   '}]})
    assert result['selectedBullets'][0]['text'] == 'Built billing.'
    assert result['selectedBullets'][0]['rewritten'] is False


def test_editing_back_to_the_original_clears_the_rewritten_flag():
    result = tailor.apply_edits(BASE, {'bullets': [
        {'bulletId': 'b1', 'text': 'Built billing.'},
    ]})
    assert result['selectedBullets'][0]['rewritten'] is False


def test_bullet_text_is_bounded():
    result = tailor.apply_edits(BASE, {'bullets': [
        {'bulletId': 'b1', 'text': 'x' * 5000},
    ]})
    assert len(result['selectedBullets'][0]['text']) == tailor.MAX_BULLET_CHARS


def test_the_summary_is_bounded():
    result = tailor.apply_edits(BASE, {'summary': 'word ' * 200, 'bullets': []})
    assert len(result['summary'].split()) <= tailor.MAX_SUMMARY_WORDS + 1


def test_an_absent_summary_key_leaves_the_summary_alone():
    result = tailor.apply_edits(BASE, {'bullets': []})
    assert result['summary'] == 'Original summary.'


def test_keywords_survive_an_edit():
    """They are the computed coverage report, not something the user writes."""
    result = tailor.apply_edits(BASE, {'bullets': []})
    assert result['keywords'] == {'matched': ['python'], 'missing': []}


# --------------------------------------------------------------------------
# PATCH /resumes/<id>
# --------------------------------------------------------------------------

def test_an_edit_is_persisted_and_re_rendered(client, version):
    response = client.patch(f'/api/jobs/resumes/{version["versionId"]}',
                            json=bullet_patch(version['content'], 'Rebuilt billing.'))
    assert response.status_code == 200
    assert 'Rebuilt billing.' in response.get_json()['html']

    stored = get_db().execute(
        'SELECT content, html FROM resume_versions WHERE id=?', (version['versionId'],)
    ).fetchone()
    assert 'Rebuilt billing.' in stored['html']
    assert json.loads(stored['content'])['selectedBullets'][0]['text'] == 'Rebuilt billing.'


def test_a_user_written_bullet_is_not_clamped_against_the_profile(client, version):
    """`clamp` bounds the *model*. The user is the authority on their own
    resume, and re-applying that bound here would delete their edit.
    """
    invented = 'Shipped a payments platform to nine countries.'
    client.patch(f'/api/jobs/resumes/{version["versionId"]}',
                 json=bullet_patch(version['content'], invented))

    stored = json.loads(get_db().execute(
        'SELECT content FROM resume_versions WHERE id=?', (version['versionId'],)
    ).fetchone()['content'])
    assert stored['selectedBullets'][0]['text'] == invented


def test_editing_does_not_create_a_second_version(client, version):
    client.patch(f'/api/jobs/resumes/{version["versionId"]}',
                 json=bullet_patch(version['content'], 'Once.'))
    client.patch(f'/api/jobs/resumes/{version["versionId"]}',
                 json=bullet_patch(version['content'], 'Twice.'))

    count = get_db().execute(
        'SELECT COUNT(*) AS n FROM resume_versions WHERE application_id=?',
        (version['applicationId'],),
    ).fetchone()['n']
    assert count == 1


def test_editing_a_sent_application_is_refused(client, version):
    """Once it has gone out, the row is the record of what the employer got."""
    client.patch(f'/api/jobs/applications/{version["applicationId"]}',
                 json={'status': 'submitted'})

    response = client.patch(f'/api/jobs/resumes/{version["versionId"]}',
                            json=bullet_patch(version['content'], 'Too late.'))
    assert response.status_code == 409

    stored = json.loads(get_db().execute(
        'SELECT content FROM resume_versions WHERE id=?', (version['versionId'],)
    ).fetchone()['content'])
    assert stored['selectedBullets'][0]['text'] == 'Built billing in Python.'


def test_editing_a_purged_resume_is_410(client, version):
    db = get_db()
    db.execute('UPDATE resume_versions SET purged_at=1 WHERE id=?', (version['versionId'],))
    db.commit()

    response = client.patch(f'/api/jobs/resumes/{version["versionId"]}',
                            json=bullet_patch(version['content'], 'Gone.'))
    assert response.status_code == 410


def test_editing_an_unknown_resume_is_404(client):
    assert client.patch('/api/jobs/resumes/nope', json={'bullets': []}).status_code == 404


def test_edited_text_is_escaped_into_the_html(client, version):
    """The app renders `html` with dangerouslySetInnerHTML.

    Before this route the content came from a grammar-constrained model; now a
    user can type anything into a bullet, so the renderer's escaping is what
    stands between that and script execution in the preview pane.
    """
    payload = '<script>alert(1)</script>'
    response = client.patch(f'/api/jobs/resumes/{version["versionId"]}', json={
        'summary': payload,
        'bullets': [
            {'bulletId': b['bulletId'], 'text': payload}
            for b in version['content']['selectedBullets']
        ],
    })
    html = response.get_json()['html']
    assert '<script>' not in html
    assert '&lt;script&gt;' in html


# --------------------------------------------------------------------------
# The name the employer files away
# --------------------------------------------------------------------------

def test_the_filename_carries_the_users_name():
    assert render.download_filename('Ada Lovelace', 'pdf') == 'Ada Lovelace Resume.pdf'


def test_a_nameless_profile_still_gets_a_sensible_filename():
    for value in ('', '   ', None):
        assert render.download_filename(value, 'docx') == 'Resume.docx'


def test_path_separators_cannot_escape_the_filename():
    assert render.download_filename('../../etc/passwd', 'pdf') == 'etc passwd Resume.pdf'


def test_control_characters_are_stripped():
    """The value lands in a Content-Disposition header, where a bare CRLF is
    header injection."""
    result = render.download_filename('Ada\r\nX-Evil: 1', 'pdf')
    assert '\r' not in result and '\n' not in result
    assert result == 'Ada X-Evil 1 Resume.pdf'


def test_a_very_long_name_is_capped():
    result = render.download_filename('A' * 500, 'pdf')
    assert len(result) < 120


def test_accents_survive():
    assert render.download_filename('Zoë Müller', 'pdf') == 'Zoë Müller Resume.pdf'


def test_the_download_uses_the_profile_name(client, version):
    response = client.get(f'/api/jobs/resumes/{version["versionId"]}/download.pdf')
    if response.status_code == 404:
        pytest.skip('WeasyPrint unavailable, so no PDF was rendered')
    assert 'Ada Lovelace Resume.pdf' in response.headers['Content-Disposition']
