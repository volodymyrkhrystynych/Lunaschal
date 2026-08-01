from datetime import date

from backend.ai.llm import chat_json

CLASSIFIER_PROMPT = """You are an intent classifier. Analyze the user's message and determine its intent.

Intent Types:
- journal: Personal reflections, diary entries, things learned, thoughts. "Today I...", "I learned...", "I felt..."
- calendar: Activities or events. "I went to...", "Had a meeting...", mentions of times/dates.
- question: Asking for information. Question marks, "How do I...", "What is..."
- flashcard_request: Wants flashcards or quiz. "quiz me", "create flashcards"
- note_to_self: User explicitly says "note to self" (or a clear equivalent) to
  capture something worth remembering as a lesson. Extract the substance into
  content. If the phrase is used but there isn't yet enough substance to form
  a lesson (e.g. just "note to self" alone), leave content empty and keep
  confidence below 0.7 so the assistant asks what the lesson actually is.
- conversation: General chat, greetings, commands.

Rules:
1. For journal entries, clean up content while preserving voice.
2. For calendar events, determine date. Today: {TODAY}
3. Confidence: 0.8+ for clear intents, 0.5-0.8 for ambiguous.
4. Generate relevant tags.

Respond with valid JSON matching this schema:
{
  "intent": "journal|calendar|question|conversation|flashcard_request|note_to_self",
  "confidence": 0.0-1.0,
  "journalEntry": {"title": "...", "content": "...", "tags": ["..."]} (only if journal),
  "calendarEvent": {"title": "...", "description": "...", "date": "YYYY-MM-DD", "time": "HH:MM", "tags": ["..."]} (only if calendar),
  "flashcardRequest": {"topic": "..."} (only if flashcard_request),
  "noteToSelf": {"content": "..."} (only if note_to_self)
}"""


_TAGS = {'type': 'array', 'items': {'type': 'string'}}

# The intent enum is the whole point of constraining this call: every caller
# switches on `intent`, and an off-menu value (or a missing key) silently
# degrades to 'conversation'. The grammar makes those outcomes impossible.
CLASSIFIER_SCHEMA = {
    'type': 'object',
    'properties': {
        'intent': {
            'type': 'string',
            'enum': ['journal', 'calendar', 'question', 'conversation',
                     'flashcard_request', 'note_to_self'],
        },
        'confidence': {'type': 'number', 'minimum': 0, 'maximum': 1},
        'journalEntry': {'anyOf': [{
            'type': 'object',
            'properties': {'title': {'type': 'string'},
                           'content': {'type': 'string'},
                           'tags': _TAGS},
            'required': ['title', 'content'],
        }, {'type': 'null'}]},
        'calendarEvent': {'anyOf': [{
            'type': 'object',
            'properties': {'title': {'type': 'string'},
                           'description': {'type': 'string'},
                           'date': {'type': 'string'},
                           'time': {'type': 'string'},
                           'tags': _TAGS},
            'required': ['title', 'date'],
        }, {'type': 'null'}]},
        'flashcardRequest': {'anyOf': [{
            'type': 'object',
            'properties': {'topic': {'type': 'string'}},
            'required': ['topic'],
        }, {'type': 'null'}]},
        'noteToSelf': {'anyOf': [{
            'type': 'object',
            'properties': {'content': {'type': 'string'}},
            'required': ['content'],
        }, {'type': 'null'}]},
    },
    'required': ['intent', 'confidence'],
}


def should_classify(message: str) -> bool:
    msg = message.lower().strip()
    if 'note to self' in msg:
        return True
    if len(msg) < 20:
        return False
    if msg.startswith(('what ', 'how ', 'why ')):
        return False
    if msg in ('hi', 'hello', 'hey', 'thanks', 'bye'):
        return False
    return True


def classify_intent(message: str) -> dict:
    prompt = CLASSIFIER_PROMPT.replace('{TODAY}', date.today().isoformat()) + f'\n\nUser message:\n{message}'

    try:
        return chat_json(prompt, schema=CLASSIFIER_SCHEMA)
    except Exception as e:
        print(f'Classification error: {e}')

    return {'intent': 'conversation', 'confidence': 0.5}
