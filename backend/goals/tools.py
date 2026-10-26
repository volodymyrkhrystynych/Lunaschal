"""Chat-only goal tools: save rules, inspect, check now, redeem or cancel."""
import json

from backend.goals import service


def _tool(name, description, properties, required=()):
    return {'type': 'function', 'function': {'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties,
                       'required': list(required), 'additionalProperties': False}}}


TOOLS = [
    _tool('create_goal', 'Save a daily consecutive-day goal and one reward when the user asks. '
          'Rules are immutable: establish the criterion, start day and prize first. '
          'For a repetition exercise goal supply exercise and minimumReps; these define its '
          'criterion for BOTH workouts and journal evidence. Do not use exercise for goals '
          'with additional conditions; describe those fully in criteria for journal evaluation.', {
        'title': {'type': 'string'}, 'criteria': {'type': 'string'}, 'reward': {'type': 'string'},
        'targetDays': {'type': 'integer', 'minimum': 1, 'maximum': 366},
        'startDate': {'type': 'string', 'description': 'YYYY-MM-DD; default today (4am day)'},
        'exercise': {'type': 'string'}, 'minimumReps': {'type': 'integer', 'minimum': 1},
    }, ['title', 'criteria', 'reward', 'targetDays']),
    _tool('list_goals', 'Read saved goals, progress and reward status. Use before creating a '
          'goal referred to earlier, so it is not recreated. Progress is from the last check.', {}),
    _tool('check_goal', 'Recalculate a saved goal NOW from workouts and journal entries, '
          'including the current partial day. Can earn the reward immediately; no need to '
          'wait until evening. Use for progress/reward questions; never infer a win yourself.',
          {'goalId': {'type': 'string'}}, ['goalId']),
    _tool('update_goal', 'Cancel a goal or mark an earned prize redeemed only when the user '
          'asks. Redeeming verifies the evidence again.',
          {'goalId': {'type': 'string'}, 'action': {'type': 'string', 'enum': ['cancel','redeem']}},
          ['goalId','action']),
]
TOOL_NAMES = {t['function']['name'] for t in TOOLS}


def run_tool(name, args):
    event = {'tool': name, 'arg': json.dumps(args), 'ok': True}
    try:
        if name == 'create_goal':
            result = service.create(args)
        elif name == 'list_goals':
            result = service.list_goals()
        elif name == 'check_goal':
            result = service.evaluate(args.get('goalId', ''))
        elif name == 'update_goal':
            result = service.change_status(args.get('goalId', ''), args.get('action'))
        else:
            raise ValueError('Unknown goal tool')
        if isinstance(result, dict):
            event['title'] = result['title']
        # Full evidence stays in the UI; keep the gathering transcript bounded.
        def compact(goal):
            return {k:v for k,v in goal.items() if k != 'days'} | {
                'unresolvedDays': [d['date'] for d in goal['days'] if d['verdict'] == 'uncertain']}
        result = [compact(g) for g in result] if isinstance(result, list) else compact(result)
        return json.dumps(result), event
    except (ValueError, LookupError) as exc:
        event.update(ok=False, error=str(exc))
        return str(exc), event
