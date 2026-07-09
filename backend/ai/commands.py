import json
from datetime import date
from backend.ai.provider import get_provider_config, DEFAULT_MODELS

COMMAND_PROMPT = """You are a voice command parser for a personal assistant. The user spoke a command \
which was transcribed by speech-to-text (so expect minor transcription errors). Decide what they want \
done and extract the details.

Today is {TODAY} ({WEEKDAY}).

Actions:
- create_todo: add an item to the todo list. "Create a todo about...", "remind me to...", "add ... to my list"
- create_event: add a calendar event. "There's an event on...", "I have a meeting...", "schedule..."
- create_journal: save a journal entry. "Journal that...", "make a note that today I..."
- create_recipe: save a recipe to the cookbook. "Save this recipe...", "add a recipe for..."
- clarify: something essential is missing or genuinely ambiguous — ask ONE short question.
- none: the command doesn't match any action; explain briefly in "speak".

Rules:
1. Resolve relative dates ("tomorrow", "next Friday", "the 11th") to YYYY-MM-DD using today's date. \
A bare day like "the 11th" means the next upcoming 11th.
2. Only use clarify when you truly cannot act — e.g. an event with no recoverable date, or an empty command. \
Do NOT ask about optional details (time, description, tags): omit them instead.
3. Titles should be short and cleaned up (drop filler like "create a todo about").
4. "speak" is read aloud by TTS: one short plain-text sentence, no markdown, no emojis. For completed \
actions it confirms what was done, e.g. "Added a todo: buy milk." For clarify it is the question itself.
5. The conversation may contain earlier clarifying questions and answers — combine everything into one final action.

Respond with valid JSON matching this schema:
{
  "action": "create_todo|create_event|create_journal|create_recipe|clarify|none",
  "speak": "one sentence, spoken aloud",
  "todo": {"title": "..."} (only if create_todo),
  "event": {"title": "...", "date": "YYYY-MM-DD", "time": "HH:MM" (optional), "description": "..." (optional)} (only if create_event),
  "journal": {"content": "..."} (only if create_journal),
  "recipe": {"title": "...", "content": "the recipe as spoken, ingredients and steps"} (only if create_recipe)
}"""


def parse_voice_command(messages: list[dict]) -> dict:
    """Parse a spoken command (possibly a multi-turn clarification exchange) into an action dict."""
    c = get_provider_config()
    provider = c['provider']
    today = date.today()
    system = COMMAND_PROMPT.replace('{TODAY}', today.isoformat()).replace(
        '{WEEKDAY}', today.strftime('%A'))

    try:
        if provider in ('openai', 'ollama'):
            from openai import OpenAI
            if provider == 'openai':
                client = OpenAI(api_key=c['openai_api_key'])
                model = c['model'] or DEFAULT_MODELS['openai']
            else:
                client = OpenAI(base_url=f"{c['ollama_url']}/v1", api_key='ollama')
                model = c['ollama_model'] or c['model'] or DEFAULT_MODELS['ollama']
            resp = client.chat.completions.create(
                model=model,
                messages=[{'role': 'system', 'content': system}, *messages],
                response_format={'type': 'json_object'},
            )
            return json.loads(resp.choices[0].message.content)

        if provider == 'gemini':
            import google.generativeai as genai
            genai.configure(api_key=c['google_api_key'])
            model_name = c['model'] or DEFAULT_MODELS['gemini']
            convo = '\n'.join(f"{m['role']}: {m['content']}" for m in messages)
            resp = genai.GenerativeModel(model_name).generate_content(
                f'{system}\n\nConversation:\n{convo}',
                generation_config={'response_mime_type': 'application/json'},
            )
            return json.loads(resp.text)

    except Exception as e:
        print(f'Voice command parse error: {e}')

    return {'action': 'none', 'speak': "Sorry, I couldn't process that command."}
