"""Tailoring: the schema bounds and the clamp that resolves the model's answer.

These are the two things standing between "tailored resume" and "resume with
invented experience on it", so they are tested without a model in the loop.
"""
from backend.jobs import keywords as kw
from backend.jobs import profile as profile_mod
from backend.jobs import tailor

BULLETS = [
    {'index': 0, 'id': 'b0', 'roleId': 'r1', 'company': 'Acme', 'title': 'Engineer',
     'text': 'Built the billing service.'},
    {'index': 1, 'id': 'b1', 'roleId': 'r1', 'company': 'Acme', 'title': 'Engineer',
     'text': 'Cut page load time in half.'},
]

REPORT = kw.KeywordReport(matched=['python'], missing=['kubernetes'])


# --- the schema bound -----------------------------------------------------

def test_bullet_index_is_bounded_to_the_real_list():
    schema = tailor.build_schema(len(BULLETS), REPORT.matched)
    index = schema['properties']['selectedBullets']['items']['properties']['index']
    assert index == {'type': 'integer', 'minimum': 0, 'maximum': 1}


def test_empty_profile_permits_no_selection_at_all():
    schema = tailor.build_schema(0, [])
    assert schema['properties']['selectedBullets']['maxItems'] == 0


def test_emphasis_is_an_enum_of_supported_keywords_only():
    schema = tailor.build_schema(2, ['python', 'postgresql'])
    assert schema['properties']['emphasis']['items'] == {
        'type': 'string', 'enum': ['python', 'postgresql'],
    }


def test_no_supported_keywords_means_no_emphasis_field():
    """Nothing can be claimed, so the field the model would claim it in is gone."""
    assert 'emphasis' not in tailor.build_schema(2, [])['properties']


# --- the clamp ------------------------------------------------------------

def test_clamp_resolves_indexes_to_real_bullets():
    result = tailor.clamp(
        {'summary': 'Backend engineer.',
         'selectedBullets': [{'index': 1, 'rewritten': 'Halved page load time.'}]},
        BULLETS, REPORT,
    )
    assert len(result['selectedBullets']) == 1
    selected = result['selectedBullets'][0]
    assert selected['bulletId'] == 'b1'
    assert selected['roleId'] == 'r1'
    assert selected['text'] == 'Halved page load time.'
    assert selected['original'] == 'Cut page load time in half.'
    assert selected['rewritten'] is True


def test_clamp_drops_an_out_of_range_index():
    """Belt and braces behind the grammar — this must never reach a document."""
    result = tailor.clamp(
        {'summary': '', 'selectedBullets': [
            {'index': 0, 'rewritten': 'ok'},
            {'index': 99, 'rewritten': 'I ran a division of 400 people.'},
        ]},
        BULLETS, REPORT,
    )
    assert [s['index'] for s in result['selectedBullets']] == [0]


def test_clamp_drops_duplicate_selections():
    result = tailor.clamp(
        {'summary': '', 'selectedBullets': [
            {'index': 0, 'rewritten': 'first'},
            {'index': 0, 'rewritten': 'again'},
        ]},
        BULLETS, REPORT,
    )
    assert len(result['selectedBullets']) == 1
    assert result['selectedBullets'][0]['text'] == 'first'


def test_clamp_falls_back_to_the_original_when_the_rewrite_is_empty():
    result = tailor.clamp(
        {'summary': '', 'selectedBullets': [{'index': 0, 'rewritten': '   '}]},
        BULLETS, REPORT,
    )
    assert result['selectedBullets'][0]['text'] == 'Built the billing service.'
    assert result['selectedBullets'][0]['rewritten'] is False


def test_clamp_rejects_unsupported_emphasis_keywords():
    """A keyword the profile cannot back must not survive to the document."""
    result = tailor.clamp(
        {'summary': '', 'selectedBullets': [], 'emphasis': ['python', 'kubernetes']},
        BULLETS, REPORT,
    )
    assert result['emphasis'] == ['python']


def test_clamp_truncates_an_overlong_summary():
    long_summary = ' '.join(['word'] * 200)
    result = tailor.clamp(
        {'summary': long_summary, 'selectedBullets': []}, BULLETS, REPORT
    )
    assert len(result['summary'].split()) <= tailor.MAX_SUMMARY_WORDS + 1


def test_clamp_survives_malformed_model_output():
    for garbage in ({}, {'selectedBullets': None}, {'selectedBullets': ['nope']},
                    {'selectedBullets': [{'index': 'x'}]}):
        result = tailor.clamp(garbage, BULLETS, REPORT)
        assert result['selectedBullets'] == []


def test_clamp_always_carries_the_computed_keyword_report():
    result = tailor.clamp({'summary': '', 'selectedBullets': []}, BULLETS, REPORT)
    assert result['keywords']['matched'] == ['python']
    assert result['keywords']['missing'] == ['kubernetes']


# --- the prompt -----------------------------------------------------------

def test_prompt_labels_missing_keywords_as_forbidden():
    prompt = tailor.build_prompt(BULLETS, {'title': 'Engineer'}, REPORT)
    assert 'MISSING' in prompt and 'kubernetes' in prompt
    assert 'SUPPORTED' in prompt and 'python' in prompt


def test_prompt_numbers_bullets_to_match_their_indexes():
    prompt = tailor.build_prompt(BULLETS, {'title': 'Engineer'}, REPORT)
    assert '0. [Acme — Engineer] Built the billing service.' in prompt
    assert '1. [Acme — Engineer] Cut page load time in half.' in prompt


def test_steer_is_included_but_subordinated_to_the_rules():
    prompt = tailor.build_prompt(
        BULLETS, {'title': 'Engineer'}, REPORT, steer='make me sound senior'
    )
    assert 'make me sound senior' in prompt
    assert 'do not support' in prompt


# --- unavailable model ----------------------------------------------------

def test_returns_none_rather_than_an_untailored_fallback(monkeypatch):
    """A generic resume that pretends to be tailored is indistinguishable
    after the fact, so failure has to be visible."""
    monkeypatch.setattr(tailor, 'is_ai_configured', lambda: False)
    assert tailor.tailor_resume({'roles': [], 'skills': []}, {'description': ''}) is None


def test_returns_none_when_the_model_raises(monkeypatch):
    monkeypatch.setattr(tailor, 'is_ai_configured', lambda: True)

    def _boom(*a, **k):
        raise RuntimeError('llama-server is down')

    monkeypatch.setattr(tailor, 'chat_json', _boom)
    assert tailor.tailor_resume({'roles': [], 'skills': []}, {'description': ''}) is None


def test_end_to_end_with_a_stubbed_model(monkeypatch):
    loaded = {
        'profile': {'headline': 'Engineer', 'summary': ''},
        'roles': [{'id': 'r1', 'company': 'Acme', 'title': 'Engineer', 'bullets': [
            {'id': 'b0', 'text': 'Built billing in Python.', 'tags': []},
        ]}],
        'skills': [{'name': 'Python'}],
        'education': [], 'answers': [],
    }
    monkeypatch.setattr(tailor, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(tailor, 'chat_json', lambda *a, **k: {
        'summary': 'Backend engineer.',
        'selectedBullets': [{'index': 0, 'rewritten': 'Built billing in Python.'}],
        'emphasis': ['python'],
    })

    result = tailor.tailor_resume(loaded, {'description': 'We use Python and Kubernetes.'})
    assert result['selectedBullets'][0]['bulletId'] == 'b0'
    assert result['emphasis'] == ['python']
    assert 'kubernetes' in result['keywords']['missing']


def test_flat_bullets_indexes_match_their_position():
    loaded = {
        'roles': [
            {'id': 'r1', 'company': 'A', 'title': 'T', 'bullets': [
                {'id': 'b0', 'text': 'one'}, {'id': 'b1', 'text': 'two'}]},
            {'id': 'r2', 'company': 'B', 'title': 'U', 'bullets': [
                {'id': 'b2', 'text': 'three'}]},
        ],
    }
    bullets = profile_mod.flat_bullets(loaded)
    assert [b['index'] for b in bullets] == [0, 1, 2]
    assert [b['id'] for b in bullets] == ['b0', 'b1', 'b2']
    assert bullets[2]['roleId'] == 'r2'
