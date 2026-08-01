"""Structure a freeform workout note into exercises and sets.

Entry stays freeform because that's the existing habit (docs/lifestyle-tab.md §2):

    bicep curls 20,10 20,10
    squats 60,8 60,8 65,6

Same pattern as `backend/ai/recipes.py` / `backend/ai/food.py`: raw text in,
JSON out, grammar-constrained by a schema. The raw text is kept on the row, so
returning None here costs nothing but the parse.
"""
from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured

_MAX_INPUT_CHARS = 8000

_WORKOUT_SYSTEM = (
    "You convert a freeform gym log into structured data. Each line is usually "
    "one exercise followed by its sets. Sets are written many ways — "
    '"20,10" and "20x10" and "20 kg x 10" all mean weight 20, reps 10; '
    '"3x12" with no weight means three sets of 12 reps at bodyweight.\n'
    "Return ONLY valid JSON: an object with an \"exercises\" array, each item "
    '{"name": str, "sets": [{"weight": number|null, "reps": integer|null}]}.\n'
    'Rules:\n'
    '- "name" is the exercise exactly as the user wrote it, minus the numbers. '
    "Do not rename, expand, or correct it — the caller handles naming.\n"
    "- Expand shorthand: \"3x12\" becomes three separate set objects.\n"
    "- Use null for a weight that was not given (bodyweight work), never 0. "
    "Never guess or invent a weight, and never copy one in from another line.\n"
    "- A run of bare numbers with no weight paired to them is one rep count per "
    'set, at bodyweight. "squats 10 10 10 10" is four sets: '
    '[{"weight": null, "reps": 10}, {"weight": null, "reps": 10}, '
    '{"weight": null, "reps": 10}, {"weight": null, "reps": 10}].\n'
    "- Leave out anything that is not an exercise (how you felt, the weather, "
    "where you trained). Never invent sets that were not written."
)

_WORKOUT_SCHEMA = {
    'type': 'object',
    'properties': {
        'exercises': {
            'type': 'array',
            'maxItems': 40,
            'items': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'sets': {
                        'type': 'array',
                        'maxItems': 30,
                        'items': {
                            'type': 'object',
                            # Both keys are required *and* nullable: the grammar
                            # then forces an explicit `"weight": null` for a
                            # bodyweight set ("squats 10 10 10 10") instead of
                            # letting the model drop the key and leave "no
                            # weight" and "forgot to say" indistinguishable.
                            'properties': {
                                'weight': {'type': ['number', 'null']},
                                'reps': {'type': ['integer', 'null']},
                            },
                            'required': ['weight', 'reps'],
                        },
                    },
                },
                'required': ['name', 'sets'],
            },
        },
    },
    'required': ['exercises'],
}


def _number(value):
    """A finite, non-negative number, or None. Rejects bools (isinstance(True, int))."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float('inf'), float('-inf')) or value < 0:
        return None
    return value


def parse_workout(text: str) -> list[dict] | None:
    """Structure raw workout text into [{name, sets: [{weight, reps}]}].

    Returns None when AI is unconfigured or the call failed — the caller keeps
    the raw text and can retry later. An empty list is a real answer ("nothing
    in this note was an exercise") and is distinct from None.
    """
    if not text.strip() or not is_ai_configured():
        return None
    try:
        data = chat_json(text[:_MAX_INPUT_CHARS], system=_WORKOUT_SYSTEM, schema=_WORKOUT_SCHEMA)
    except Exception as e:
        print(f'Workout parsing failed: {e}')
        return None
    if not isinstance(data, dict) or not isinstance(data.get('exercises'), list):
        return None

    exercises = []
    for raw in data['exercises']:
        if not isinstance(raw, dict):
            continue
        name = raw.get('name')
        if not isinstance(name, str) or not name.strip():
            continue
        sets = []
        for raw_set in raw.get('sets') or []:
            if not isinstance(raw_set, dict):
                continue
            weight = _number(raw_set.get('weight'))
            reps = _number(raw_set.get('reps'))
            reps = int(reps) if reps is not None else None
            # A set with neither number carries nothing; drop it rather than
            # storing a row that would show up as a blank point on the chart.
            if weight is None and reps is None:
                continue
            sets.append({'weight': weight, 'reps': reps})
        exercises.append({'name': name.strip(), 'sets': sets})
    return exercises
