from backend.jobs import draft_review


def test_reviewer_revision_is_clamped_to_real_bullets(monkeypatch):
    loaded = {'profile': {'fullName': 'A'}, 'roles': [{'id': 'r', 'company': 'Acme',
        'title': 'Engineer', 'bullets': [{'id': 'b', 'text': 'Built Python services'}]}],
        'skills': [{'name': 'Python'}], 'answers': [], 'links': []}
    monkeypatch.setattr(draft_review, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(draft_review, 'chat_json', lambda *a, **k: {
        'summary': 'Backend engineer', 'selectedBullets': [
            {'index': 0, 'rewritten': 'Built Python services'},
            {'index': 99, 'rewritten': 'Invented'}], 'emphasis': ['Python'],
        'critique': ['Make Python explicit']})
    result = draft_review.review_once(loaded, {'description': 'Python'}, {})
    assert [b['bulletId'] for b in result['selectedBullets']] == ['b']
    assert result['draftReview'] == ['Make Python explicit']
