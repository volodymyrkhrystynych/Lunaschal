"""Tests for the Practice tab: pure session-scoring and drill-mode logic (no
DB) plus the Flask routes end-to-end against a throwaway DB via the `client`
fixture."""
import pytest

from backend.ai import practice as ai_practice
from backend.practice import modes, queue
from backend.practice.explanations import EXPLANATIONS, explanation_for
from backend.practice.grading import fallback_grade, normalize_code, rating_label
from backend.practice.snippets import SNIPPETS


FAKE_SNIPPETS = [
    {'id': 'a', 'language': 'javascript', 'category': 'loops', 'title': 'A', 'code': 'a'},
    {'id': 'b', 'language': 'javascript', 'category': 'loops', 'title': 'B', 'code': 'b'},
    {'id': 'c', 'language': 'css', 'category': 'layout', 'title': 'C', 'code': 'c'},
]


def fluent(**overrides) -> dict:
    """Progress for a snippet typed well enough that blind runs have unlocked."""
    return {
        'attempts_count': 4,
        'last_accuracy': 100.0,
        'last_wpm': 40.0,
        'best_accuracy': 100.0,
        'best_wpm': 40.0,
        'recall_attempts_count': 0,
        'recall_passes': 0,
        'last_recall_passed': None,
        **overrides,
    }


def test_build_session_prioritizes_unattempted_then_weak_snippets():
    now = 1_000_000
    progress = {
        'a': {'attempts_count': 5, 'last_accuracy': 99.0, 'last_wpm': 80.0, 'last_practiced_at': now},
        'b': {'attempts_count': 3, 'last_accuracy': 50.0, 'last_wpm': 20.0, 'last_practiced_at': now},
    }
    ids = queue.build_session(FAKE_SNIPPETS, progress, now=now, size=3)
    # 'c' is never attempted -> always first; 'b' is weaker than 'a' -> scores higher
    assert ids == ['c', 'b', 'a']


def test_build_session_respects_language_filter():
    ids = queue.build_session(FAKE_SNIPPETS, {}, size=10, language='css')
    assert ids == ['c']


def test_build_session_respects_category_filter():
    ids = queue.build_session(FAKE_SNIPPETS, {}, size=10, category='layout')
    assert ids == ['c']


def test_build_session_caps_result_size():
    ids = queue.build_session(FAKE_SNIPPETS, {}, size=2)
    assert len(ids) == 2


def test_build_session_recency_breaks_ties_between_equally_weak_snippets():
    now = 1_000_000
    progress = {
        'a': {
            'attempts_count': 1, 'last_accuracy': 90.0, 'last_wpm': 40.0,
            'last_practiced_at': now - 86400,  # practiced 1 day ago
        },
        'b': {
            'attempts_count': 1, 'last_accuracy': 90.0, 'last_wpm': 40.0,
            'last_practiced_at': now - 86400 * 10,  # practiced 10 days ago
        },
    }
    ids = queue.build_session(FAKE_SNIPPETS, progress, now=now, size=3)
    assert ids == ['c', 'b', 'a']


def test_build_session_is_ordered_worst_first_not_shuffled():
    """A batch larger than one snippet must come back strictly ranked, since
    the frontend now walks it as "next lowest, next lowest" rather than
    treating it as a shuffled bag of equally-due snippets."""
    now = 1_000_000
    progress = {
        'a': {'attempts_count': 5, 'last_accuracy': 95.0, 'last_wpm': 60.0, 'last_practiced_at': now},
        'b': {'attempts_count': 5, 'last_accuracy': 40.0, 'last_wpm': 10.0, 'last_practiced_at': now},
    }
    for _ in range(10):
        ids = queue.build_session(FAKE_SNIPPETS, progress, now=now, size=3)
        assert ids == ['c', 'b', 'a']


def test_build_session_ranks_a_failed_recall_above_a_sloppily_typed_snippet():
    """Couldn't-write-it-from-memory is the strongest signal in the bank: the
    snippet is typed fluently and still not known, which no accuracy penalty on
    a snippet that is merely being typed badly should be able to outrank."""
    now = 1_000_000
    progress = {
        'a': fluent(last_practiced_at=now, last_recall_passed=0, recall_attempts_count=1),
        'b': {
            'attempts_count': 5, 'last_accuracy': 0.0, 'last_wpm': 0.0,
            'last_practiced_at': now,
        },
    }
    ids = queue.build_session(FAKE_SNIPPETS, progress, now=now, size=3)
    assert ids == ['c', 'a', 'b']  # 'c' is unattempted, then the failed recall


def test_build_session_does_not_penalize_a_snippet_never_asked_from_memory():
    """`last_recall_passed` is NULL until the first blind run. Reading that as a
    failure would push every fluent snippet to the front of the queue."""
    now = 1_000_000
    progress = {
        'a': fluent(last_practiced_at=now),
        'b': fluent(last_practiced_at=now, last_accuracy=90.0),
    }
    ids = queue.build_session(FAKE_SNIPPETS, progress, now=now, size=3)
    assert ids == ['c', 'b', 'a']  # ranked by accuracy alone, no recall effect


# --- drill mode -------------------------------------------------------------


def test_next_mode_is_speed_until_the_snippet_is_typed_well():
    assert modes.next_mode(None) == modes.SPEED
    assert modes.next_mode({}) == modes.SPEED
    # One good run is not enough on its own.
    assert modes.next_mode(fluent(attempts_count=1)) == modes.SPEED
    # Typed repeatedly but inaccurately: the characters aren't known yet.
    assert modes.next_mode(fluent(best_accuracy=80.0)) == modes.SPEED
    # Accurate but far below the unlock speed.
    assert modes.next_mode(fluent(best_wpm=10.0)) == modes.SPEED


def test_next_mode_goes_blind_once_typing_it_out_is_good():
    assert modes.next_mode(fluent()) == modes.BLIND


def test_blind_share_rises_with_typing_speed():
    slow = modes.blind_share(fluent(best_wpm=modes.UNLOCK_WPM))
    mid = modes.blind_share(fluent(best_wpm=40.0))
    fast = modes.blind_share(fluent(best_wpm=modes.FLUENT_WPM))
    assert slow == pytest.approx(modes.MIN_BLIND_SHARE)
    assert fast == pytest.approx(modes.MAX_BLIND_SHARE)
    assert slow < mid < fast
    # Never all-blind: a snippet you can write from memory is still worth
    # re-copying, and that run is the only way a forgotten detail comes back.
    assert fast < 1.0
    assert modes.blind_share(fluent(best_wpm=999.0)) == pytest.approx(modes.MAX_BLIND_SHARE)


def test_a_failed_recall_sends_the_next_run_back_to_typing():
    """The fix for "I couldn't remember it" is seeing it again."""
    failed = fluent(recall_attempts_count=1, last_recall_passed=0)
    assert modes.next_mode(failed) == modes.SPEED
    # A passed one leaves the normal mix in charge.
    passed = fluent(recall_attempts_count=1, recall_passes=1, last_recall_passed=1)
    assert modes.next_mode(passed) in (modes.SPEED, modes.BLIND)


def _walk(runs: int, best_wpm: float) -> list[str]:
    """Play out `runs` drills of one snippet, every one of them going well."""
    progress = fluent(attempts_count=modes.UNLOCK_ATTEMPTS, best_wpm=best_wpm)
    sequence = []
    for _ in range(runs):
        mode = modes.next_mode(progress)
        sequence.append(mode)
        if mode == modes.BLIND:
            progress['recall_attempts_count'] += 1
            progress['recall_passes'] += 1
            progress['last_recall_passed'] = 1
        else:
            progress['attempts_count'] += 1
    return sequence


def test_a_barely_unlocked_snippet_mostly_gets_typing_drills():
    sequence = _walk(12, best_wpm=modes.UNLOCK_WPM)
    assert sequence[0] == modes.BLIND  # earned it, so it is asked straight away
    blind = sequence.count(modes.BLIND)
    assert 2 <= blind <= 4, sequence  # ~a quarter of twelve


def test_a_fluently_typed_snippet_mostly_gets_asked_from_memory():
    """"The better I get with speed, the more blind runs it gives me" — the
    same walk over a faster snippet has to come out with more of them."""
    slow = _walk(12, best_wpm=modes.UNLOCK_WPM).count(modes.BLIND)
    fast = _walk(12, best_wpm=modes.FLUENT_WPM).count(modes.BLIND)
    assert fast > slow
    assert fast >= 7, fast  # three quarters of twelve, less the two speed runs owed


def test_the_mix_follows_speed_as_it_improves():
    """The ratio is driven off realized counts, not off a cadence fixed when the
    snippet unlocked — so getting faster changes the mix from that point on."""
    progress = fluent(attempts_count=9, recall_attempts_count=3, recall_passes=3,
                      last_recall_passed=1, best_wpm=modes.UNLOCK_WPM)
    assert modes.next_mode(progress) == modes.SPEED  # 25% of runs recalled, 25% owed
    progress['best_wpm'] = modes.FLUENT_WPM
    assert modes.next_mode(progress) == modes.BLIND  # same history, 75% owed


# --- grading ----------------------------------------------------------------


def test_rating_label_thresholds():
    assert rating_label(60, 50) == 'Needs work'
    assert rating_label(85, 10) == 'Good'
    assert rating_label(96, 10) == 'Good'  # accurate but too slow
    assert rating_label(99, 45) == 'Great'


def test_every_snippet_has_a_prompt():
    """The blind drill shows the prompt *instead of* the code, so a snippet
    without one is a drill that cannot be asked."""
    missing = [s['id'] for s in SNIPPETS if not (s.get('prompt') or '').strip()]
    assert missing == []


def test_every_snippet_has_an_explanation():
    """The explanations are keyed by snippet id in a separate file, so the two
    lists can drift apart silently — a snippet added without one simply renders
    no panel, which reads as a broken feature rather than a missing entry."""
    ids = {s['id'] for s in SNIPPETS}
    assert sorted(ids - set(EXPLANATIONS)) == []
    # And nothing keyed to a snippet that no longer exists.
    assert sorted(set(EXPLANATIONS) - ids) == []


def test_every_explanation_names_at_least_one_part():
    """A summary on its own is a paragraph; the per-part lines are what make it
    readable against the code that was just typed."""
    thin = [
        snippet_id
        for snippet_id in EXPLANATIONS
        if not explanation_for(snippet_id)['parts']
        or not explanation_for(snippet_id)['summary'].strip()
    ]
    assert thin == []


def test_normalize_code_folds_formatting_but_not_content():
    assert normalize_code('const a = 1;') == normalize_code('const   a=1 ;')
    assert normalize_code('()   => {\n  go();\n}') == normalize_code('()=>{go();}')
    # Quotes are left alone: eating a space beside one edits a string literal.
    assert normalize_code("say('hi there')") != normalize_code("say('hithere')")
    assert normalize_code('const a = 1;') != normalize_code('const b = 1;')


def test_fallback_grade_passes_a_match_and_says_it_was_a_text_comparison():
    passed = fallback_grade('const a = 1;', 'const  a=1;')
    assert passed['passed'] is True
    assert passed['gradedBy'] == 'fallback'

    failed = fallback_grade('const a = 1;', 'let a = 1')
    assert failed['passed'] is False
    assert failed['verdict'] == 'wrong'
    # The label is the point: this verdict cannot tell a wrong answer from a
    # differently-written correct one, and the UI renders that caveat from it.
    assert failed['gradedBy'] == 'fallback'


def test_grade_recall_reads_the_verdict_from_the_model(monkeypatch):
    monkeypatch.setattr(ai_practice, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(
        ai_practice, 'chat_json',
        lambda *a, **k: {'verdict': 'partial', 'feedback': 'The dependency array is missing.'},
    )
    result = ai_practice.grade_recall(
        title='useEffect', task='Call fetchData when id changes', language='react',
        reference='useEffect(() => {}, [id]);', submitted='useEffect(() => {});',
    )
    assert result['verdict'] == 'partial'
    assert result['passed'] is False  # only "correct" passes
    assert result['gradedBy'] == 'model'
    assert 'dependency' in result['feedback']


def test_grade_recall_accepts_an_equivalent_answer_the_model_approves(monkeypatch):
    """The grader's verdict is final — a `correct` on text that differs from the
    reference must not be second-guessed by a diff afterwards."""
    monkeypatch.setattr(ai_practice, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(
        ai_practice, 'chat_json',
        lambda *a, **k: {'verdict': 'correct', 'feedback': 'Same thing, named differently.'},
    )
    result = ai_practice.grade_recall(
        title='useState', task='Declare state `count` starting at 0', language='react',
        reference='const [count, setCount] = useState(0);',
        submitted='const [ count , setCount ] = useState( 0 )',
    )
    assert result['passed'] is True


def test_grade_recall_falls_back_to_a_text_comparison_when_the_model_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError('llama-server unreachable')

    monkeypatch.setattr(ai_practice, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(ai_practice, 'chat_json', boom)

    matched = ai_practice.grade_recall(
        title='useState', task='...', language='react',
        reference='const [count, setCount] = useState(0);',
        submitted='const [count, setCount] = useState(0)',  # no semicolon
    )
    assert matched['passed'] is True
    assert matched['gradedBy'] == 'fallback'

    differed = ai_practice.grade_recall(
        title='useState', task='...', language='react',
        reference='const [count, setCount] = useState(0);',
        submitted='const [n, setN] = useState(0);',
    )
    assert differed['passed'] is False
    assert differed['gradedBy'] == 'fallback'


def test_grade_recall_does_not_spend_a_generation_on_an_empty_answer(monkeypatch):
    def fail(*a, **k):
        raise AssertionError('the model must not be called for an empty submission')

    monkeypatch.setattr(ai_practice, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(ai_practice, 'chat_json', fail)
    result = ai_practice.grade_recall(
        title='useState', task='...', language='react', reference='x', submitted='   \n ',
    )
    assert result['passed'] is False
    assert result['gradedBy'] == 'empty'


def test_session_endpoint_filters_by_language(client):
    resp = client.get('/api/practice/session?language=css&size=3')
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) <= 3
    assert data
    assert all(s['language'] == 'css' for s in data)


def test_submit_attempt_updates_progress_and_returns_rating(client):
    snippet_id = SNIPPETS[0]['id']
    resp = client.post(
        '/api/practice/attempts',
        json={'snippetId': snippet_id, 'wpm': 45, 'accuracy': 98, 'errorCount': 1},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['rating'] == 'Great'
    assert data['progress']['attemptsCount'] == 1
    assert data['progress']['lastWpm'] == 45
    assert data['progress']['snippetId'] == snippet_id

    resp2 = client.post(
        '/api/practice/attempts',
        json={'snippetId': snippet_id, 'wpm': 30, 'accuracy': 70, 'errorCount': 5},
    )
    data2 = resp2.get_json()
    assert data2['rating'] == 'Needs work'
    assert data2['progress']['attemptsCount'] == 2
    assert data2['progress']['bestWpm'] == 45
    assert data2['progress']['bestAccuracy'] == 98


def test_submit_attempt_rejects_unknown_snippet(client):
    resp = client.post(
        '/api/practice/attempts', json={'snippetId': 'nope', 'wpm': 1, 'accuracy': 1}
    )
    assert resp.status_code == 400


def test_session_serves_speed_drills_with_the_code(client):
    data = client.get('/api/practice/session?size=3').get_json()
    assert data
    assert all(d['mode'] == 'speed' for d in data)  # nothing practiced yet
    assert all(d['code'] for d in data)
    # The code is already on screen in a speed drill, so what it means comes with it.
    assert all(d['explanation']['summary'] for d in data)


def _make_fluent(client, snippet_id):
    """Type a snippet out well enough that its blind runs unlock."""
    for _ in range(modes.UNLOCK_ATTEMPTS):
        client.post(
            '/api/practice/attempts',
            json={'snippetId': snippet_id, 'wpm': 45, 'accuracy': 100, 'errorCount': 0},
        )


def test_a_blind_drill_withholds_the_code_and_carries_the_prompt(client):
    snippet = SNIPPETS[0]
    _make_fluent(client, snippet['id'])

    # Narrowed to the snippet's own category: the queue ranks worst-first, so a
    # snippet that has just been practiced well sits at the very back of the bank.
    data = client.get(
        f"/api/practice/session?language={snippet['language']}"
        f"&category={snippet['category']}&size=50"
    ).get_json()
    drill = next(d for d in data if d['id'] == snippet['id'])
    assert drill['mode'] == 'blind'
    assert drill['prompt'] == snippet['prompt']
    # The whole drill is "write it without seeing it": shipping the answer to
    # the browser would put it one devtools panel away.
    assert 'code' not in drill
    # The explanation names every field the reference uses, so it is withheld on
    # the same grounds and arrives with the grade instead.
    assert 'explanation' not in drill


def test_recall_endpoint_records_a_pass_and_reveals_the_reference(client, monkeypatch):
    monkeypatch.setattr(ai_practice, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(
        ai_practice, 'chat_json',
        lambda *a, **k: {'verdict': 'correct', 'feedback': 'Spot on.'},
    )
    snippet = SNIPPETS[0]
    resp = client.post(
        '/api/practice/recall',
        json={'snippetId': snippet['id'], 'submitted': 'const [count, setCount] = useState(0);'},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['passed'] is True
    assert data['gradedBy'] == 'model'
    # The reference arrives with the grade and not before it, and so does the
    # explanation of what was being recalled.
    assert data['reference'] == snippet['code']
    assert data['explanation'] == explanation_for(snippet['id'])
    assert data['progress']['recallAttemptsCount'] == 1
    assert data['progress']['recallPasses'] == 1
    assert data['progress']['lastRecallPassed'] == 1
    # A blind run is not a typing attempt and must not move the typing numbers.
    assert data['progress']['attemptsCount'] == 0


def test_recall_failure_sends_the_snippet_back_to_a_typing_drill(client, monkeypatch):
    monkeypatch.setattr(ai_practice, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(
        ai_practice, 'chat_json',
        lambda *a, **k: {'verdict': 'wrong', 'feedback': 'That is a different hook.'},
    )
    snippet = SNIPPETS[0]
    _make_fluent(client, snippet['id'])
    session_url = (
        f"/api/practice/session?language={snippet['language']}"
        f"&category={snippet['category']}&size=50"
    )
    assert next(
        d for d in client.get(session_url).get_json() if d['id'] == snippet['id']
    )['mode'] == 'blind'

    resp = client.post(
        '/api/practice/recall', json={'snippetId': snippet['id'], 'submitted': 'useRef()'}
    )
    assert resp.get_json()['progress']['lastRecallPassed'] == 0

    drill = next(
        d for d in client.get(session_url).get_json() if d['id'] == snippet['id']
    )
    assert drill['mode'] == 'speed'
    assert drill['code'] == snippet['code']


def test_recall_counters_accumulate_across_attempts(client, monkeypatch):
    verdicts = iter(['correct', 'wrong', 'correct'])
    monkeypatch.setattr(ai_practice, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(
        ai_practice, 'chat_json',
        lambda *a, **k: {'verdict': next(verdicts), 'feedback': ''},
    )
    snippet_id = SNIPPETS[0]['id']
    for _ in range(3):
        resp = client.post(
            '/api/practice/recall', json={'snippetId': snippet_id, 'submitted': 'x'}
        )
    progress = resp.get_json()['progress']
    assert progress['recallAttemptsCount'] == 3
    assert progress['recallPasses'] == 2
    assert progress['lastRecallPassed'] == 1


def test_recall_endpoint_rejects_unknown_snippet(client):
    resp = client.post(
        '/api/practice/recall', json={'snippetId': 'nope', 'submitted': 'x'}
    )
    assert resp.status_code == 400


def test_stats_reports_recall_separately_from_typing(client, monkeypatch):
    monkeypatch.setattr(ai_practice, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(
        ai_practice, 'chat_json', lambda *a, **k: {'verdict': 'correct', 'feedback': ''}
    )
    empty = client.get('/api/practice/stats').get_json()['recall']
    # Never asked to recall anything is not the same state as 0% recalled.
    assert empty == {'attempts': 0, 'passes': 0, 'passRate': None}

    client.post(
        '/api/practice/recall', json={'snippetId': SNIPPETS[0]['id'], 'submitted': 'x'}
    )
    stats = client.get('/api/practice/stats').get_json()
    assert stats['recall'] == {'attempts': 1, 'passes': 1, 'passRate': 100.0}
    # The typing averages are computed over practice_attempts alone, so a blind
    # run leaves them empty rather than dragging a 0 wpm through them.
    assert stats['totalAttempts'] == 0


def test_snippet_listing_carries_each_explanation(client):
    data = client.get('/api/practice/snippets?language=css').get_json()
    assert data
    assert all(s['explanation']['parts'] for s in data)


def test_stats_endpoint_aggregates_by_language(client):
    snippet_id = next(s['id'] for s in SNIPPETS if s['language'] == 'html')
    client.post(
        '/api/practice/attempts',
        json={'snippetId': snippet_id, 'wpm': 50, 'accuracy': 100, 'errorCount': 0},
    )
    resp = client.get('/api/practice/stats')
    data = resp.get_json()
    assert data['totalAttempts'] == 1
    assert data['byLanguage']['html']['attempts'] == 1
    assert data['byLanguage']['css']['attempts'] == 0
