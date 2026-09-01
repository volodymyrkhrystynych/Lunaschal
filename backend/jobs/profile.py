"""Reading the master profile out of the DB in the shapes the rest of the
module wants.

Every function here returns plain dicts and lists and holds no transaction, so
a caller can load the profile, commit, and *then* spend thirty seconds in the
model — the rule `backend/research/worker.py` sets out, for the reason it sets
out: `get_db()` is one process-global connection and a background thread's
open transaction would be committed by whichever Flask handler commits next.
"""
import json

from backend.db.connection import row_to_dict


def _json_list(value) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def load_profile(db) -> dict:
    """The whole profile: contact block, roles with their bullets, skills,
    education and the saved answer bank."""
    row = db.execute('SELECT * FROM job_profile WHERE id=1').fetchone()
    profile = row_to_dict(row) if row else {}
    profile['links'] = _json_list(profile.get('links'))
    profile['companyBlacklist'] = _json_list(profile.get('companyBlacklist'))
    profile['avoidClearanceRoles'] = bool(profile.get('avoidClearanceRoles'))

    roles = []
    for role_row in db.execute('SELECT * FROM profile_roles ORDER BY ord, created_at').fetchall():
        role = row_to_dict(role_row)
        bullets = db.execute(
            'SELECT * FROM profile_bullets WHERE role_id=? ORDER BY ord, created_at',
            (role['id'],),
        ).fetchall()
        role['bullets'] = []
        for b in bullets:
            bullet = row_to_dict(b)
            bullet['tags'] = _json_list(bullet.get('tags'))
            role['bullets'].append(bullet)
        roles.append(role)

    skills = []
    for s in db.execute('SELECT * FROM profile_skills ORDER BY ord, name').fetchall():
        skills.append(row_to_dict(s))

    education = []
    for e in db.execute('SELECT * FROM profile_education ORDER BY ord, created_at').fetchall():
        education.append(row_to_dict(e))

    answers = []
    for a in db.execute('SELECT * FROM profile_answers ORDER BY ord, created_at').fetchall():
        answer = row_to_dict(a)
        answer['tags'] = _json_list(answer.get('tags'))
        answers.append(answer)

    return {
        'profile': profile,
        'roles': roles,
        'skills': skills,
        'education': education,
        'answers': answers,
    }


def flat_bullets(loaded: dict) -> list[dict]:
    """Every bullet as one flat, ordered list.

    The list index *is* the address the model is given and the bound its schema
    is built from, so this ordering is load-bearing: it must be deterministic
    and it must be the same list that resolves the model's answer.
    """
    out = []
    for role in loaded.get('roles', []):
        for bullet in role.get('bullets', []):
            out.append({
                'index': len(out),
                'id': bullet['id'],
                'roleId': role['id'],
                'company': role.get('company', ''),
                'title': role.get('title', ''),
                'text': bullet.get('text', ''),
            })
    return out


def skill_names(loaded: dict) -> list[str]:
    return [s['name'] for s in loaded.get('skills', []) if s.get('name')]


def profile_text(loaded: dict) -> str:
    """Everything the user has written about themselves, as one blob.

    Used for keyword matching, where a bullet describing a Kubernetes migration
    should count as evidence of Kubernetes even if it was never typed into the
    skills list.
    """
    parts = [
        loaded.get('profile', {}).get('headline', ''),
        loaded.get('profile', {}).get('summary', ''),
    ]
    for role in loaded.get('roles', []):
        parts.append(role.get('title', ''))
        parts.append(role.get('company', ''))
        parts.extend(b.get('text', '') for b in role.get('bullets', []))
    parts.extend(skill_names(loaded))
    for e in loaded.get('education', []):
        parts.extend([e.get('credential', ''), e.get('field', ''), e.get('notes', '')])
    parts.extend(a.get('answer', '') for a in loaded.get('answers', []))
    return '\n'.join(p for p in parts if p)


def is_empty(loaded: dict) -> bool:
    """True when there is nothing to tailor from."""
    return not flat_bullets(loaded) and not skill_names(loaded)
