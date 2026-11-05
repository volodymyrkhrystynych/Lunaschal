"""Chat goal tools: set, edit, inspect, check now, redeem, end or cancel."""
import json

from backend.goals import service


def _tool(name, description, properties, required=()):
    return {'type': 'function', 'function': {'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties,
                       'required': list(required), 'additionalProperties': False}}}


TOOLS = [
    _tool('create_goal', 'Save a goal for this week, month, quarter or year when the user asks. '
          'Most goals are just a title (and optional details). Only when the user wants a '
          'daily streak with a prize, add criteria and a reward: that rule is immutable, so '
          'establish the criterion, start day and prize first. For a repetition exercise rule '
          'supply exercise and minimumReps; these define its criterion for BOTH workouts and '
          'journal evidence. Do not use exercise for rules with additional conditions; describe '
          'those fully in criteria for journal evaluation. For the overarching goal use '
          'set_overarching_goal instead.', {
        'horizon': {'type': 'string', 'enum': ['week', 'month', 'quarter', 'year']},
        'title': {'type': 'string'}, 'details': {'type': 'string'},
        'criteria': {'type': 'string'}, 'reward': {'type': 'string'},
        'targetDays': {'type': 'integer', 'minimum': 1, 'maximum': 366,
                       'description': 'Streak length for a criteria goal; default the rest of the period'},
        'startDate': {'type': 'string', 'description': 'YYYY-MM-DD; default today (4am day)'},
        'exercise': {'type': 'string'}, 'minimumReps': {'type': 'integer', 'minimum': 1},
    }, ['horizon', 'title']),
    _tool('set_overarching_goal', 'Replace the one overarching goal, only when the user '
          'explicitly asks to change it. Pass their full new wording.',
          {'text': {'type': 'string'}}, ['text']),
    _tool('edit_goal', 'Rename or re-describe a week/month/quarter/year goal when the user asks. '
          'Its criteria and reward cannot change.',
          {'goalId': {'type': 'string'}, 'title': {'type': 'string'}, 'details': {'type': 'string'}},
          ['goalId']),
    _tool('list_goals', 'Read the overarching goal and the open goals for this week, month, '
          'quarter and year, with streak and reward status. Use before creating a goal '
          'referred to earlier, so it is not recreated. Progress is from the last check.', {}),
    _tool('check_goal', 'Recalculate a saved criteria goal NOW from workouts and journal entries, '
          'including the current partial day. Can earn the reward immediately; no need to '
          'wait until evening. Use for progress/reward questions; never infer a win yourself.',
          {'goalId': {'type': 'string'}}, ['goalId']),
    _tool('update_goal', 'End, cancel or redeem a goal only when the user asks. Redeeming '
          'verifies the evidence again.',
          {'goalId': {'type': 'string'}, 'action': {'type': 'string', 'enum': ['end', 'cancel', 'redeem']}},
          ['goalId','action']),
]
TOOL_NAMES = {t['function']['name'] for t in TOOLS}


def _compact(goal):
    # Full evidence stays in the UI; keep the gathering transcript bounded.
    return {k: v for k, v in goal.items() if k != 'days'} | {
        'unresolvedDays': [d['date'] for d in goal['days'] if d['verdict'] == 'uncertain']}


def run_tool(name, args):
    event = {'tool': name, 'arg': json.dumps(args), 'ok': True}
    try:
        if name == 'create_goal':
            result = service.create(args)
        elif name == 'set_overarching_goal':
            result = service.set_overarching(args.get('text'))
        elif name == 'edit_goal':
            result = service.edit(args.get('goalId', ''),
                                  {k: v for k, v in args.items() if k in ('title', 'details')})
        elif name == 'list_goals':
            current = service.current()
            event['title'] = 'Current goals'
            return json.dumps({
                'overarching': current['overarching'] and _compact(current['overarching']),
                'horizons': [h | {'goals': [_compact(g) for g in h['goals']]} for h in current['horizons']],
            }), event
        elif name == 'check_goal':
            result = service.evaluate(args.get('goalId', ''))
        elif name == 'update_goal':
            result = service.change_status(args.get('goalId', ''), args.get('action'))
        else:
            raise ValueError('Unknown goal tool')
        event['title'] = result['title']
        return json.dumps(_compact(result)), event
    except (ValueError, LookupError) as exc:
        event.update(ok=False, error=str(exc))
        return str(exc), event
