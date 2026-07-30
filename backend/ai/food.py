from backend.ai.llm import chat_json
from backend.tags import normalize_tags
from backend.ai.provider import is_ai_configured

_MAX_INPUT_CHARS = 15000

_FOOD_SYSTEM = (
    "You clean up and structure a food-log note (typed or spoken) about a meal "
    "the user ate. The note is often a rambling voice transcript that wanders "
    "across several topics. Your job is to tidy it WITHOUT losing anything.\n"
    "Return ONLY valid JSON with these fields:\n"
    '- "notes": the user\'s note rewritten cleanly — fix filler words, false '
    "starts, run-ons, and transcription errors, but PRESERVE EVERY topic, "
    "detail, and aside they mentioned. Never summarize, condense, or drop "
    "anything; keep it in their own first-person voice and roughly the same "
    "length. Null only if there was genuinely no commentary at all\n"
    '- "dish": a short name for the main thing they ate (e.g. "Tonkotsu '
    'ramen"), or null if unclear\n'
    '- "place": where they ate it — a restaurant, "home", etc. — or null\n'
    '- "rating": an integer 1-5 for how good it was (5 = loved it, 1 = bad), or '
    "null if they gave no opinion\n"
    '- "tags": an array of 1-5 lowercase tags (cuisine, meal type, main '
    'ingredient), e.g. ["japanese", "ramen", "dinner"]\n'
    '- "recipe": if and only if they described how they made it, an object '
    '{"title": str, "content": markdown with "## Ingredients" and '
    '"## Instructions", "tags": [str]}; otherwise null\n'
    "dish/place/rating/tags/recipe are extra metadata pulled out ON TOP of the "
    "full notes — they never replace content in notes. Never invent a recipe "
    "they did not describe."
)

# Everything but `notes` is nullable — the prompt explicitly asks for null when a
# detail wasn't mentioned, and inventing a dish name or rating would be worse than
# omitting it. `rating` gets its 1-5 bound enforced by the grammar, which is
# exactly the check parse_food_entry has to do by hand below.
_RECIPE_OBJECT = {
    'type': 'object',
    'properties': {
        'title': {'type': 'string'},
        'content': {'type': 'string'},
        'tags': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 5},
    },
    'required': ['title', 'content'],
}

_FOOD_SCHEMA = {
    'type': 'object',
    'properties': {
        'notes': {'type': ['string', 'null']},
        'dish': {'type': ['string', 'null']},
        'place': {'type': ['string', 'null']},
        'rating': {'anyOf': [{'type': 'integer', 'minimum': 1, 'maximum': 5},
                             {'type': 'null'}]},
        'tags': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 5},
        'recipe': {'anyOf': [_RECIPE_OBJECT, {'type': 'null'}]},
    },
    'required': ['notes'],
}


def parse_food_entry(text: str) -> dict | None:
    """Structure a raw food note into {dish, place, rating, notes, tags, recipe}.

    Returns None when AI is unconfigured or nothing usable could be parsed, so
    the caller can fall back to the raw text. `recipe` is a nested
    {title, content, tags} dict or None.
    """
    if not text.strip() or not is_ai_configured():
        return None
    text = text[:_MAX_INPUT_CHARS]
    try:
        data = chat_json(text, system=_FOOD_SYSTEM, schema=_FOOD_SCHEMA)
    except Exception as e:
        print(f'Food entry parsing failed: {e}')
        return None
    if not isinstance(data, dict):
        return None

    def _str(key):
        v = data.get(key)
        return v.strip() if isinstance(v, str) and v.strip() else None

    rating = data.get('rating')
    if isinstance(rating, bool) or not isinstance(rating, int) or not (1 <= rating <= 5):
        rating = None

    tags = normalize_tags(data.get('tags'))[:5]

    recipe = None
    raw_recipe = data.get('recipe')
    if isinstance(raw_recipe, dict):
        r_title = raw_recipe.get('title')
        r_content = raw_recipe.get('content')
        if isinstance(r_title, str) and r_title.strip() and isinstance(r_content, str) and r_content.strip():
            r_tags = normalize_tags(raw_recipe.get('tags'))[:5]
            recipe = {'title': r_title.strip(), 'content': r_content.strip(), 'tags': r_tags}

    return {
        'dish': _str('dish'),
        'place': _str('place'),
        'rating': rating,
        'notes': _str('notes'),
        'tags': tags,
        'recipe': recipe,
    }
