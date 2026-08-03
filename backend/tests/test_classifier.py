"""Intent classification: gating (`should_classify`) and the note-to-self intent."""
from backend.ai import classifier


def test_should_classify_gates_short_messages():
    assert classifier.should_classify('hi') is False
    assert classifier.should_classify('ok') is False


def test_should_classify_always_runs_for_note_to_self():
    # A bare "note to self" is short enough that the length gate would
    # normally drop it, but that's exactly the case that needs the model to
    # run so the assistant can ask what the lesson actually is.
    assert classifier.should_classify('note to self') is True
    assert classifier.should_classify('Note to self!') is True


def test_classify_intent_returns_note_to_self(monkeypatch):
    captured = {}

    def fake_chat_json(prompt, schema=None):
        captured['prompt'] = prompt
        return {
            'intent': 'note_to_self',
            'confidence': 0.9,
            'noteToSelf': {'content': 'always warm up before deadlifts'},
        }

    monkeypatch.setattr(classifier, 'chat_json', fake_chat_json)
    result = classifier.classify_intent('note to self: always warm up before deadlifts')
    assert result['intent'] == 'note_to_self'
    assert result['noteToSelf']['content'] == 'always warm up before deadlifts'


def test_classify_intent_falls_back_to_conversation_on_error(monkeypatch):
    def raise_error(prompt, schema=None):
        raise RuntimeError('boom')

    monkeypatch.setattr(classifier, 'chat_json', raise_error)
    result = classifier.classify_intent('note to self')
    assert result == {'intent': 'conversation', 'confidence': 0.5}
