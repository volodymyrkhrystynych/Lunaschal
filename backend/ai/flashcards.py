import json
from backend.ai.provider import get_provider_config, DEFAULT_MODELS

GENERATOR_PROMPT = """You are a flashcard generator. Given content, create effective flashcards for spaced repetition.

Guidelines:
1. Extract key facts, concepts, definitions, and relationships
2. Create clear, concise questions that test understanding
3. Answers should be brief but complete
4. Use different question types: definitions, fill-in-blank, cause-effect, comparisons
5. Focus on the most important information
6. Each flashcard should test ONE concept
7. Generate 3-7 flashcards depending on content density

Respond with valid JSON: {"flashcards": [{"front": "...", "back": "..."}, ...]}"""

TOPIC_PROMPT = """You are a flashcard generator. Create 5-8 flashcards to help someone learn about the given topic.
Cover key concepts, definitions, and facts. Include a mix of basic and advanced questions.

{CONTEXT}

Respond with valid JSON: {"flashcards": [{"front": "...", "back": "..."}, ...]}"""


def _call_ai(prompt: str) -> list[dict]:
    c = get_provider_config()
    provider = c['provider']

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
            messages=[{'role': 'user', 'content': prompt}],
            response_format={'type': 'json_object'},
        )
        return json.loads(resp.choices[0].message.content)['flashcards']

    if provider == 'gemini':
        import google.generativeai as genai
        genai.configure(api_key=c['google_api_key'])
        model_name = c['model'] or DEFAULT_MODELS['gemini']
        resp = genai.GenerativeModel(model_name).generate_content(
            prompt,
            generation_config={'response_mime_type': 'application/json'},
        )
        return json.loads(resp.text)['flashcards']

    raise ValueError(f'Unknown provider: {provider}')


def generate_flashcards_from_content(content: str, title: str | None = None) -> list[dict]:
    text = f'Title: {title}\n\nContent:\n{content}' if title else content
    return _call_ai(f'{GENERATOR_PROMPT}\n\n**Content:**\n{text}')


def generate_flashcards_for_topic(topic: str, context: str | None = None) -> list[dict]:
    ctx_section = f'Additional context from user\'s notes:\n{context}\n\n' if context else ''
    return _call_ai(TOPIC_PROMPT.replace('{CONTEXT}', ctx_section) + f'Topic: {topic}')
