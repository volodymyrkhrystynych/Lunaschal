from backend.ai.llm import chat_json
from backend.tags import normalize_tags
from backend.ai.provider import is_ai_configured

_MAX_INPUT_CHARS = 15000

_RECIPE_SYSTEM = (
    "You extract a recipe from raw text (pasted notes or scraped webpage text). "
    "Ignore navigation menus, ads, comments, and life stories — keep only the recipe itself.\n"
    "Return ONLY valid JSON with these fields:\n"
    '- "title": a short recipe name\n'
    '- "content": the full recipe as clean markdown — an "## Ingredients" bulleted list and an '
    '"## Instructions" numbered list, preserving quantities exactly as written; include yield '
    "and prep/cook times if present\n"
    '- "tags": an array of 1-5 lowercase tags describing the recipe (cuisine, meal type, '
    'main ingredient), e.g. ["italian", "dinner", "chicken"]\n'
    'If the text contains no recipe, return {"title": null}.'
)

# title/content are nullable because "no recipe here" is a real answer the prompt
# asks for; parse_recipe turns either being empty into None.
_RECIPE_SCHEMA = {
    'type': 'object',
    'properties': {
        'title': {'type': ['string', 'null']},
        'content': {'type': ['string', 'null']},
        'tags': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 5},
    },
    'required': ['title'],
}


def parse_recipe(text: str) -> dict | None:
    """Extract {title, content, tags} from raw text, or None if no recipe was found."""
    if not text.strip():
        return None
    text = text[:_MAX_INPUT_CHARS]
    try:
        if not is_ai_configured():
            return None
        data = chat_json(text, system=_RECIPE_SYSTEM, schema=_RECIPE_SCHEMA)

        title = (data.get('title') or '').strip() if isinstance(data.get('title'), str) else ''
        content = (data.get('content') or '').strip() if isinstance(data.get('content'), str) else ''
        if not title or not content:
            return None
        tags = normalize_tags(data.get('tags'))[:5]
        return {'title': title, 'content': content, 'tags': tags}
    except Exception as e:
        print(f'Recipe parsing failed: {e}')

    return None
