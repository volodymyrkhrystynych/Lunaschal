from backend.jobs import resume_review


LOADED = {
    'profile': {'fullName': 'Ada Lovelace', 'email': 'ada@example.com',
                'phone': '416-555-0100', 'summary': 'Engineer'},
    'roles': [{'id': 'r1'}], 'skills': [{'name': 'Python'}], 'education': [],
}
CONTENT = {
    'summary': 'Engineer',
    'selectedBullets': [
        {'text': 'Built Python services used by 2,000 customers.'},
        {'text': 'Helped with operations.'},
    ],
    'keywords': {'matched': ['Python']},
}


def test_review_checks_the_rendered_text_layer_and_local_metrics(tmp_path, monkeypatch):
    pdf = tmp_path / 'resume.pdf'
    pdf.write_bytes(b'%PDF')
    monkeypatch.setattr(resume_review, 'extract_pdf_text', lambda path:
                        'Ada Lovelace ada@example.com 416-555-0100\nSummary\nEngineer\nExperience\nBuilt Python services\nSkills\nPython')
    result = resume_review.review(LOADED, CONTENT, pdf_path=pdf)
    assert result['parseable'] is True
    assert result['readingOrder'] is True
    assert result['keywordChecks']['coverage'] == 1
    assert result['metrics']['actionVerbDensity'] == 0.5
    assert result['metrics']['quantifiedImpactDensity'] == 0.5
    assert result['issues'] == []


def test_missing_contact_and_keyword_are_actionable_issues(tmp_path, monkeypatch):
    pdf = tmp_path / 'resume.pdf'; pdf.write_bytes(b'%PDF')
    monkeypatch.setattr(resume_review, 'extract_pdf_text', lambda path:
                        'Summary ' + ('plain text ' * 20) + ' Experience Skills')
    result = resume_review.review(LOADED, CONTENT, pdf_path=pdf)
    assert any('Missing from PDF text' in issue for issue in result['issues'])
    assert any('Python' in issue for issue in result['issues'])
