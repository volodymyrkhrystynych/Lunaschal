from datetime import date

from backend.ai.llm import chat_json

CLASSIFIER_PROMPT = """You are an intent classifier. Analyze the user's message and determine its intent.

Intent Types:
- calendar: Activities or events. "I went to...", "Had a meeting...", mentions of times/dates.
- question: Asking for information. Question marks, "How do I...", "What is..."
- flashcard_request: Wants flashcards or quiz. "quiz me", "create flashcards"
- conversation: General chat, greetings, commands.

Rules:
1. For calendar events, determine date. Today: {TODAY}
2. Confidence: 0.8+ for clear intents, 0.5-0.8 for ambiguous.
3. Generate relevant tags.

Respond with valid JSON matching this schema:
{
  "intent": "calendar|question|conversation|flashcard_request",
  "confidence": 0.0-1.0,
  "calendarEvent": {"title": "...", "description": "...", "date": "YYYY-MM-DD", "time": "HH:MM", "tags": ["..."]} (only if calendar),
  "flashcardRequest": {"topic": "..."} (only if flashcard_request)
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
            'enum': ['calendar', 'question', 'conversation',
                     'flashcard_request'],
        },
        'confidence': {'type': 'number', 'minimum': 0, 'maximum': 1},
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
    },
    'required': ['intent', 'confidence'],
}


def should_classify(message: str) -> bool:
    msg = message.lower().strip()
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
