"""Per-application Q&A: what was actually typed into one employer's form.

The Answer Kit route generates answers and persists nothing. These are the
record, and the properties worth pinning down are the ones that decide whether
that record is still trustworthy a year later: a multi-page form must not
overwrite its own earlier pages, and the retention sweep must not eat it.
"""
import time

import pytest

from backend.db.connection import get_db
from backend.jobs import retention


@pytest.fixture
def application(client):
    job_id = client.post('/api/jobs', json={
        'title': 'Engineer', 'company': 'Acme', 'description': 'Python.',
    }).get_json()['id']
    return client.post('/api/jobs/applications', json={'jobId': job_id}).get_json()['id']


def record(client, application_id, answers):
    return client.post(
        f'/api/jobs/applications/{application_id}/recorded-answers',
        json={'answers': answers},
    )


def test_answers_round_trip(client, application):
    response = record(client, application, [
        {'question': 'Why us?', 'answer': 'Because.', 'source': 'generated',
         'pageUrl': 'https://boards.greenhouse.io/acme/jobs/1'},
    ])
    assert response.status_code == 200
    assert response.get_json()['written'] == 1

    listed = client.get(
        f'/api/jobs/applications/{application}/recorded-answers'
    ).get_json()['answers']
    assert len(listed) == 1
    assert listed[0]['question'] == 'Why us?'
    assert listed[0]['answer'] == 'Because.'
    assert listed[0]['source'] == 'generated'
    assert listed[0]['pageUrl'] == 'https://boards.greenhouse.io/acme/jobs/1'


def test_recording_the_same_question_twice_updates_rather_than_duplicates(
    client, application
):
    """Record-as-you-fill re-sends a field the user corrected. Two rows with
    contradictory answers to one question is a record nobody can read."""
    record(client, application, [{'question': 'Salary?', 'answer': '100'}])
    record(client, application, [{'question': 'Salary?', 'answer': '120'}])

    listed = client.get(
        f'/api/jobs/applications/{application}/recorded-answers'
    ).get_json()['answers']
    assert len(listed) == 1
    assert listed[0]['answer'] == '120'


def test_a_second_page_appends_rather_than_replacing(client, application):
    """A Workday application spans several URLs. Replace-all would drop page
    one the moment page two was recorded."""
    record(client, application, [{'question': 'Name?', 'answer': 'Ada'}])
    record(client, application, [{'question': 'Notice period?', 'answer': '2 weeks'}])

    listed = client.get(
        f'/api/jobs/applications/{application}/recorded-answers'
    ).get_json()['answers']
    assert [a['question'] for a in listed] == ['Name?', 'Notice period?']


def test_order_is_the_order_they_were_recorded(client, application):
    record(client, application, [
        {'question': 'First', 'answer': '1'},
        {'question': 'Second', 'answer': '2'},
    ])
    record(client, application, [{'question': 'Third', 'answer': '3'}])

    listed = client.get(
        f'/api/jobs/applications/{application}/recorded-answers'
    ).get_json()['answers']
    assert [a['question'] for a in listed] == ['First', 'Second', 'Third']


def test_a_blank_question_is_skipped(client, application):
    """An unlabelled field is a row nobody can interpret later."""
    response = record(client, application, [
        {'question': '   ', 'answer': 'orphan'},
        {'question': 'Real', 'answer': 'kept'},
    ])
    assert response.get_json()['written'] == 1
    listed = response.get_json()['answers']
    assert [a['question'] for a in listed] == ['Real']


def test_an_unknown_source_falls_back_rather_than_being_stored(client, application):
    record(client, application, [
        {'question': 'Q', 'answer': 'A', 'source': 'telepathy'},
    ])
    listed = client.get(
        f'/api/jobs/applications/{application}/recorded-answers'
    ).get_json()['answers']
    assert listed[0]['source'] == 'generated'


def test_long_input_is_bounded(client, application):
    """These arrive from a content script in a page we do not control."""
    record(client, application, [
        {'question': 'Q' * 5000, 'answer': 'A' * 50_000},
    ])
    listed = client.get(
        f'/api/jobs/applications/{application}/recorded-answers'
    ).get_json()['answers']
    assert len(listed[0]['question']) == 500
    assert len(listed[0]['answer']) == 10_000


def test_answers_appear_on_the_application_detail(client, application):
    record(client, application, [{'question': 'Why us?', 'answer': 'Because.'}])
    detail = client.get(f'/api/jobs/applications/{application}').get_json()
    assert [a['question'] for a in detail['recordedAnswers']] == ['Why us?']


def test_recording_against_an_unknown_application_is_404(client):
    assert record(client, 'nope', [{'question': 'Q', 'answer': 'A'}]).status_code == 404


def test_an_answer_can_be_deleted(client, application):
    record(client, application, [{'question': 'Q', 'answer': 'A'}])
    answer_id = client.get(
        f'/api/jobs/applications/{application}/recorded-answers'
    ).get_json()['answers'][0]['id']

    deleted = client.delete(
        f'/api/jobs/applications/{application}/recorded-answers/{answer_id}'
    )
    assert deleted.status_code == 200
    assert client.get(
        f'/api/jobs/applications/{application}/recorded-answers'
    ).get_json()['answers'] == []


def test_deleting_across_applications_is_refused(client, application):
    """The id alone must not be enough — it is scoped to its application."""
    record(client, application, [{'question': 'Q', 'answer': 'A'}])
    answer_id = client.get(
        f'/api/jobs/applications/{application}/recorded-answers'
    ).get_json()['answers'][0]['id']

    other_job = client.post('/api/jobs', json={'title': 'Other', 'company': 'B'})
    other = client.post(
        '/api/jobs/applications', json={'jobId': other_job.get_json()['id']}
    ).get_json()['id']

    assert client.delete(
        f'/api/jobs/applications/{other}/recorded-answers/{answer_id}'
    ).status_code == 404


def test_deleting_the_application_takes_its_answers(client, application):
    record(client, application, [{'question': 'Q', 'answer': 'A'}])
    client.delete(f'/api/jobs/applications/{application}')

    remaining = get_db().execute(
        'SELECT COUNT(*) AS n FROM application_answers WHERE application_id=?',
        (application,),
    ).fetchone()['n']
    assert remaining == 0


def test_the_retention_sweep_leaves_recorded_answers_alone(client, application):
    """Retention deletes rendered files, never the record of what was said.

    "What did I tell these people?" is the question asked a year later, right
    before an interview — long after the PDF is gone.
    """
    record(client, application, [{'question': 'Why us?', 'answer': 'Because.'}])

    db = get_db()
    long_ago = int(time.time()) - 400 * 86_400
    db.execute(
        "UPDATE applications SET status='submitted', applied_at=?, purge_after=?"
        ' WHERE id=?',
        (long_ago, long_ago, application),
    )
    db.commit()

    result = retention.run_purge_sweep()
    assert result['applications'] == 1

    listed = client.get(
        f'/api/jobs/applications/{application}/recorded-answers'
    ).get_json()['answers']
    assert [a['answer'] for a in listed] == ['Because.']
