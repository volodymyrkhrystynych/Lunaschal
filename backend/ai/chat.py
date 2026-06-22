from backend.ai.provider import get_provider_config, DEFAULT_MODELS

SYSTEM_PROMPT = """You are Lunaschal, a helpful personal AI assistant. You help users with:
- Journaling and reflection
- Tracking activities and events
- Creating and reviewing flashcards for learning
- General questions and conversation

Be concise, helpful, and friendly. When users share personal experiences or daily activities,
acknowledge them warmly. If something seems like a journal entry or activity log, you can
offer to save it for them.

When context from the user's knowledge base is provided, use it to give more personalized and
relevant responses. Reference their past journal entries, activities, or notes naturally."""


def chat_stream(messages: list[dict], rag_context: str = '', system_prompt: str = ''):
    c = get_provider_config()
    provider = c['provider']

    system = system_prompt or SYSTEM_PROMPT
    if rag_context:
        system = f"{system}\n\n{rag_context}"

    all_messages = [{'role': 'system', 'content': system}] + messages

    if provider in ('openai', 'ollama'):
        from openai import OpenAI
        if provider == 'openai':
            client = OpenAI(api_key=c['openai_api_key'])
            model = c['model'] or DEFAULT_MODELS['openai']
        else:
            client = OpenAI(base_url=f"{c['ollama_url']}/v1", api_key='ollama')
            model = c['ollama_model'] or c['model'] or DEFAULT_MODELS['ollama']

        stream = client.chat.completions.create(model=model, messages=all_messages, stream=True)
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    elif provider == 'gemini':
        import google.generativeai as genai
        genai.configure(api_key=c['google_api_key'])
        model_name = c['model'] or DEFAULT_MODELS['gemini']
        gemini = genai.GenerativeModel(model_name, system_instruction=system)
        gemini_msgs = [
            {'role': 'user' if m['role'] == 'user' else 'model', 'parts': [m['content']]}
            for m in messages
        ]
        response = gemini.generate_content(gemini_msgs, stream=True)
        for chunk in response:
            if chunk.text:
                yield chunk.text
