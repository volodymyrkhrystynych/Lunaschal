"""Local ZIM tools for the shared research loop."""
from backend.offline_knowledge import archive

TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'local_knowledge_search',
            'description': (
                'Search the user\'s offline Kiwix/ZIM reference library. Use this '
                'before web research for factual or reference lookups. Supply two to '
                'four complementary queries in one call: the clean entity or title, '
                'the full question, and relevant interpretations such as book versus '
                'movie. Results are merged and title-ranked candidates; read the best '
                'one to three before relying on them.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'queries': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'minItems': 2,
                        'maxItems': 4,
                        'description': (
                            'Complementary search variants. Put the clean entity/title '
                            'first; do not add facts that are not in the user\'s question.'
                        ),
                    },
                },
                'required': ['queries'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'local_knowledge_read',
            'description': 'Read one local article returned by local_knowledge_search.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'archiveId': {'type': 'string'},
                    'path': {'type': 'string'},
                },
                'required': ['archiveId', 'path'],
            },
        },
    },
]


def run_tool(name: str, args: dict) -> tuple[str, dict]:
    if name == 'local_knowledge_search':
        # Accept the retired singular shape for old saved/tool-test callers;
        # the offered schema requires the batched form for all new turns.
        queries = args.get('queries')
        if not isinstance(queries, list):
            queries = [args.get('query')] if args.get('query') else []
        return archive.model_search(queries)
    if name == 'local_knowledge_read':
        return archive.model_read(
            str(args.get('archiveId') or ''), str(args.get('path') or '')
        )
    return f'Unknown local knowledge tool: {name}', {
        'tool': name, 'ok': False, 'error': 'Unknown tool',
    }
