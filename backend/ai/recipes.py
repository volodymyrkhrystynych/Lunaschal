from backend.ai.llm import chat_json
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


def parse_recipe(text: str) -> dict | None:
    """Extract {title, content, tags} from raw text, or None if no recipe was found."""
    if not text.strip():
        return None
    text = text[:_MAX_INPUT_CHARS]
    try:
        if not is_ai_configured():
            return None
        data = chat_json(text, system=_RECIPE_SYSTEM)

        title = (data.get('title') or '').strip() if isinstance(data.get('title'), str) else ''
        content = (data.get('content') or '').strip() if isinstance(data.get('content'), str) else ''
        if not title or not content:
            return None
        tags = [t.strip().lower() for t in (data.get('tags') or []) if isinstance(t, str) and t.strip()][:5]
        return {'title': title, 'content': content, 'tags': tags}
    except Exception as e:
        print(f'Recipe parsing failed: {e}')

    return None
