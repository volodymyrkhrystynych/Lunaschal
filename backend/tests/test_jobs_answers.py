"""The Answer Kit: resolution order, per-question schema bounds, rendering."""
from backend.jobs import answers, render

LOADED = {
    'profile': {
        'fullName': 'Ada Lovelace',
        'full_name': 'Ada Lovelace',
        'email': 'ada@example.com',
        'phone': '+1 416 555 0100',
        'location': 'Toronto, ON',
        'headline': 'Backend engineer',
        'summary': 'Builds billing systems.',
        'links': [
            {'label': 'GitHub', 'url': 'https://github.com/ada'},
            {'label': 'Portfolio', 'url': 'https://ada.dev'},
        ],
    },
    'roles': [{'id': 'r1', 'company': 'Acme', 'title': 'Engineer', 'bullets': [
        {'id': 'b0', 'text': 'Built billing in Python.'},
    ]}],
    'skills': [{'name': 'Python', 'category': 'Languages'}],
    'education': [],
    'answers': [
        {'slug': 'work_authorization', 'question': 'Are you legally authorized to work?',
         'answer': 'Yes, I am a Canadian citizen.'},
        {'slug': 'salary', 'question': 'What is your salary expectation?',
         'answer': '$140,000–160,000 CAD'},
    ],
}

JOB = {'title': 'Backend Engineer', 'company': 'Globex', 'description': 'Python work.'}


def no_model(monkeypatch):
    monkeypatch.setattr(answers, 'is_ai_configured', lambda: False)


# --- resolution order -----------------------------------------------------

def test_contact_fields_come_from_the_profile_not_the_model(monkeypatch):
    """Asking a language model to reproduce a phone number is how you get a
    phone number that is nearly right."""
    no_model(monkeypatch)
    result = answers.answer_questions(
        [{'label': 'Email address'}, {'label': 'Phone number'},
         {'label': 'Full name'}, {'label': 'City'}],
        LOADED, JOB,
    )
    assert [r['answer'] for r in result] == [
        'ada@example.com', '+1 416 555 0100', 'Ada Lovelace', 'Toronto, ON',
    ]
    assert {r['source'] for r in result} == {'profile'}


def test_links_are_matched_by_label_and_host(monkeypatch):
    no_model(monkeypatch)
    result = answers.answer_questions(
        [{'label': 'GitHub URL'}, {'label': 'Personal website'}], LOADED, JOB
    )
    assert result[0]['answer'] == 'https://github.com/ada'
    assert result[1]['answer'] == 'https://ada.dev'


def test_first_and_last_name_are_split(monkeypatch):
    no_model(monkeypatch)
    result = answers.answer_questions(
        [{'label': 'First name'}, {'label': 'Last name'}], LOADED, JOB
    )
    assert [r['answer'] for r in result] == ['Ada', 'Lovelace']


def test_bank_answers_a_reworded_question(monkeypatch):
    """Different words, same question — token overlap, not string distance."""
    no_model(monkeypatch)
    result = answers.answer_questions(
        [{'label': 'Are you legally authorized to work in Canada?'}], LOADED, JOB
    )
    assert result[0]['source'] == 'bank'
    assert result[0]['answer'] == 'Yes, I am a Canadian citizen.'


def test_unrelated_question_does_not_match_the_bank(monkeypatch):
    no_model(monkeypatch)
    result = answers.answer_questions(
        [{'label': 'Describe a time you disagreed with a colleague.'}], LOADED, JOB
    )
    assert result[0]['source'] == 'unanswered'


def test_the_model_only_sees_what_profile_and_bank_could_not_answer(monkeypatch):
    seen = {}

    def fake_chat_json(prompt, **kwargs):
        seen['prompt'] = prompt
        seen['schema'] = kwargs.get('schema')
        return {'q0': 'Because you work on payments.'}

    monkeypatch.setattr(answers, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(answers, 'chat_json', fake_chat_json)

    result = answers.answer_questions(
        [{'label': 'Email address'}, {'label': 'Why do you want to work here?'}],
        LOADED, JOB,
    )
    assert result[0]['source'] == 'profile'
    assert result[1]['source'] == 'generated'
    # One question reached the model, so its schema has exactly one property.
    assert list(seen['schema']['properties']) == ['q0']
    assert 'Email address' not in seen['prompt']


def test_no_model_call_at_all_when_everything_resolves(monkeypatch):
    """The 'just double-tap' path: instant and free."""
    def _boom(*a, **k):
        raise AssertionError('the model must not be called')

    monkeypatch.setattr(answers, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(answers, 'chat_json', _boom)
    result = answers.answer_questions([{'label': 'Email'}], LOADED, JOB)
    assert result[0]['answer'] == 'ada@example.com'


# --- per-question schema bounds -------------------------------------------

def test_dropdown_becomes_an_enum_of_its_real_options():
    schema = answers.build_schema([
        {'label': 'Work authorization', 'type': 'select',
         'options': ['Citizen', 'Permanent resident', 'Visa required']},
    ])
    assert schema['properties']['q0'] == {
        'type': 'string', 'enum': ['Citizen', 'Permanent resident', 'Visa required'],
    }


def test_boolean_and_number_fields_are_constrained():
    schema = answers.build_schema([
        {'label': 'Willing to relocate?', 'type': 'boolean'},
        {'label': 'Years of Python', 'type': 'number'},
    ])
    assert schema['properties']['q0'] == {'type': 'string', 'enum': ['Yes', 'No']}
    assert schema['properties']['q1'] == {'type': 'integer'}


def test_every_question_is_required():
    schema = answers.build_schema([{'label': 'a'}, {'label': 'b'}])
    assert schema['required'] == ['q0', 'q1']


def test_a_number_answer_is_stringified_for_the_form(monkeypatch):
    monkeypatch.setattr(answers, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(answers, 'chat_json', lambda *a, **k: {'q0': 6})
    result = answers.answer_questions(
        [{'label': 'Years of Python', 'type': 'number'}], LOADED, JOB
    )
    assert result[0]['answer'] == '6'


# --- failure modes --------------------------------------------------------

def test_a_model_failure_leaves_questions_unanswered_not_missing(monkeypatch):
    """A silently dropped field is one the user pastes a blank into."""
    def _boom(*a, **k):
        raise RuntimeError('llama-server is down')

    monkeypatch.setattr(answers, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(answers, 'chat_json', _boom)
    result = answers.answer_questions(
        [{'label': 'Why us?'}, {'label': 'Why now?'}], LOADED, JOB
    )
    assert len(result) == 2
    assert all(r['source'] == 'unanswered' for r in result)


def test_steer_reaches_the_prompt():
    prompt = answers.build_prompt(
        [{'label': 'Why us?'}], LOADED, JOB, steer='mention the payments work'
    )
    assert 'mention the payments work' in prompt


# --- rendering ------------------------------------------------------------

CONTENT = {
    'summary': 'Backend engineer who builds billing systems.',
    'selectedBullets': [
        {'bulletId': 'b0', 'roleId': 'r1', 'text': 'Built billing in Python.',
         'original': 'Built billing in Python.', 'rewritten': False},
    ],
    'emphasis': ['python'],
    'keywords': {'matched': ['python'], 'missing': ['kubernetes'], 'coverage': 0.5},
}


def test_html_contains_the_selected_bullets_under_their_role():
    html = render.render_html(LOADED, CONTENT, JOB)
    assert 'Ada Lovelace' in html
    assert 'Built billing in Python.' in html
    assert 'Acme' in html
    assert 'Backend engineer who builds billing systems.' in html


def test_html_escapes_user_text():
    loaded = {**LOADED, 'profile': {**LOADED['profile'], 'fullName': '<script>x</script>'}}
    html = render.render_html(loaded, CONTENT, JOB)
    assert '<script>' not in html
    assert '&lt;script&gt;' in html


def test_a_role_with_no_selected_bullets_still_appears():
    """Dropping a job creates an unexplained gap, which is worse than a
    heading with nothing under it."""
    content = {**CONTENT, 'selectedBullets': []}
    html = render.render_html(LOADED, content, JOB)
    assert 'Acme' in html


def test_matched_keywords_are_ordered_first_in_the_skills_block():
    loaded = {**LOADED, 'skills': [
        {'name': 'Excel', 'category': ''}, {'name': 'Python', 'category': ''},
    ]}
    ordered = render._ordered_skills(loaded, CONTENT)
    assert ordered[0]['name'] == 'Python'


def test_pdf_and_docx_render_when_available(tmp_path):
    html = render.render_html(LOADED, CONTENT, JOB)

    if render.is_pdf_available():
        pdf = tmp_path / 'r.pdf'
        assert render.render_pdf(html, pdf)
        assert pdf.read_bytes().startswith(b'%PDF')

    if render.is_docx_available():
        docx_path = tmp_path / 'r.docx'
        assert render.render_docx(LOADED, CONTENT, docx_path)
        # A .docx is a zip; the magic bytes are the cheapest real check.
        assert docx_path.read_bytes().startswith(b'PK')


def test_missing_renderer_degrades_instead_of_raising(tmp_path, monkeypatch):
    """A missing WeasyPrint costs the PDF and nothing else."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == 'weasyprint':
            raise ImportError('not installed')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', fake_import)
    assert render.is_pdf_available() is False
    assert render.render_pdf('<p>x</p>', tmp_path / 'r.pdf') is False
