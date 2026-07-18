import json
from datetime import date
from backend.ai.provider import get_provider_config, get_ollama_client, DEFAULT_MODELS

CLASSIFIER_PROMPT = """You are an intent classifier. Analyze the user's message and determine its intent.

Intent Types:
- journal: Personal reflections, diary entries, things learned, thoughts. "Today I...", "I learned...", "I felt..."
- calendar: Activities or events. "I went to...", "Had a meeting...", mentions of times/dates.
- question: Asking for information. Question marks, "How do I...", "What is..."
- flashcard_request: Wants flashcards or quiz. "quiz me", "create flashcards"
- conversation: General chat, greetings, commands.

Rules:
1. For journal entries, clean up content while preserving voice.
2. For calendar events, determine date. Today: {TODAY}
3. Confidence: 0.8+ for clear intents, 0.5-0.8 for ambiguous.
4. Generate relevant tags.

Respond with valid JSON matching this schema:
{
  "intent": "journal|calendar|question|conversation|flashcard_request",
  "confidence": 0.0-1.0,
  "journalEntry": {"title": "...", "content": "...", "tags": ["..."]} (only if journal),
  "calendarEvent": {"title": "...", "description": "...", "date": "YYYY-MM-DD", "time": "HH:MM", "tags": ["..."]} (only if calendar),
  "flashcardRequest": {"topic": "..."} (only if flashcard_request)
}"""


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
    c = get_provider_config()
    prompt = CLASSIFIER_PROMPT.replace('{TODAY}', date.today().isoformat()) + f'\n\nUser message:\n{message}'

    try:
        client = get_ollama_client(c)
        model = c['ollama_model'] or DEFAULT_MODELS['ollama']
        resp = client.chat.completions.create(
            model=model,
            messages=[{'role': 'user', 'content': prompt}],
            response_format={'type': 'json_object'},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        print(f'Classification error: {e}')

    return {'intent': 'conversation', 'confidence': 0.5}
