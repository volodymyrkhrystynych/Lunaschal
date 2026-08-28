from backend.jobs.sources import workday


def test_workday_url_parser_allows_only_known_host_shape():
    assert workday.parse_board_url(
        'https://acme.wd3.myworkdayjobs.com/en-US/External/jobs'
    ) == {'host': 'acme.wd3.myworkdayjobs.com', 'tenant': 'acme', 'site': 'External'}
    import pytest
    with pytest.raises(Exception):
        workday.parse_board_url('https://evil.example.com/en-US/External')


def test_workday_fetch_uses_cxs_json_and_full_detail(monkeypatch):
    class Response:
        def __init__(self, payload): self.payload = payload
        def raise_for_status(self): pass
        def json(self): return self.payload
    monkeypatch.setattr(workday.requests, 'post', lambda url, **kwargs: Response({
        'total': 1, 'jobPostings': [{'title': 'Platform Engineer',
          'externalPath': '/job/Toronto/Platform_R1', 'locationsText': 'Toronto',
          'bulletFields': ['R1'], 'postedOn': '2026-08-01'}]}))
    monkeypatch.setattr(workday.requests, 'get', lambda url, **kwargs: Response({
        'jobPostingInfo': {'jobReqId': 'R1', 'title': 'Platform Engineer',
          'location': 'Toronto', 'jobDescription': '<p>Build Kubernetes.</p>'}}))
    result = workday.fetch({'host': 'acme.wd3.myworkdayjobs.com',
                            'tenant': 'acme', 'site': 'External'})
    assert result.jobs[0]['sourceId'].endswith(':R1')
    assert result.jobs[0]['description'] == 'Build Kubernetes.'
    assert result.jobs[0]['url'].startswith('https://acme.wd3.myworkdayjobs.com/')


def test_workday_preserves_absolute_external_url(monkeypatch):
    class Response:
        def __init__(self, payload): self.payload = payload
        def raise_for_status(self): pass
        def json(self): return self.payload
    monkeypatch.setattr(workday.requests, 'post', lambda url, **kwargs: Response({
        'total': 1, 'jobPostings': [{'externalPath': '/job/R1'}]}))
    monkeypatch.setattr(workday.requests, 'get', lambda url, **kwargs: Response({
        'jobPostingInfo': {'jobReqId': 'R1', 'externalUrl': 'https://jobs.example.org/R1'}}))
    result = workday.fetch({'host': 'acme.wd3.myworkdayjobs.com',
                            'tenant': 'acme', 'site': 'External'})
    assert result.jobs[0]['url'] == 'https://jobs.example.org/R1'
