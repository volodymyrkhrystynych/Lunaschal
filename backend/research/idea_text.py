"""A readable name for an idea that was only ever dictated.

Voice capture stores the transcript in `raw_content` and leaves `title` empty
(backend/routes/ideas.py), and nothing fills it in afterwards — so anything
naming an idea has to derive one. The list already does, in `displayTitle`
(src/lib/ideas.ts); this is its counterpart for the server side, where the name
reaches the model's prompts and the heading of a generated plan.

Same rules as the frontend, deliberately: the first line, clipped on a word
boundary, so the two never disagree about what an idea is called.
"""

DEFAULT_LIMIT = 60
FALLBACK = 'Untitled idea'


def display_title(idea: dict, limit: int = DEFAULT_LIMIT) -> str:
    """The idea's title, or its first line clipped to `limit`."""
    title = (idea.get('title') or '').strip()
    if title:
        return title

    body = (idea.get('rawContent') or idea.get('raw_content')
            or idea.get('content') or '')
    first_line = body.strip().split('\n')[0].strip()
    if not first_line:
        return FALLBACK
    if len(first_line) <= limit:
        return first_line

    clipped = first_line[:limit]
    last_space = clipped.rfind(' ')
    kept = clipped[:last_space] if last_space > limit // 2 else clipped
    return f'{kept.rstrip()}…'
