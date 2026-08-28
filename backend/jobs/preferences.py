"""Hard job gates and non-rejecting soft preference annotations."""
import re


def _norm(value: str) -> str:
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', value.lower()).split())


def past_employers(loaded: dict) -> set[str]:
    out = set()
    for role in loaded.get('roles') or []:
        end = _norm(role.get('endLabel') or '')
        # A blank or present/current end is an ongoing role, not a past employer.
        if role.get('company') and end and end not in ('present', 'current', 'now'):
            out.add(_norm(role['company']))
    return out


def hard_gate(job: dict, loaded: dict) -> str:
    profile = loaded.get('profile') or {}
    company = _norm(job.get('company') or '')
    blacklist = {_norm(x) for x in profile.get('companyBlacklist') or [] if x}
    blacklist |= past_employers(loaded)
    if company and company in blacklist:
        return 'company is on your blacklist (past employers are included)'

    body = _norm(f"{job.get('title') or ''} {job.get('location') or ''} {job.get('description') or ''}")
    remote = bool(job.get('remote')) or bool(re.search(r'\b(remote|work from home)\b', body))
    onsite_required = bool(re.search(r'\b(on site|onsite|in office|office based)\b', body))
    if profile.get('remoteOnly') and (onsite_required or not remote):
        return 'profile requires remote work but the posting is on-site or not remote'

    allowed = [_norm(x) for x in (profile.get('allowedLocations') or '').split(',') if _norm(x)]
    location = _norm(job.get('location') or '')
    if allowed and not remote and location and not any(term in location for term in allowed):
        return f"location {job.get('location')} is outside your allowed locations"

    asks_clearance = bool(re.search(r'\b(security clearance|secret clearance|top secret|ts sci|reliability status)\b', body))
    if profile.get('avoidClearanceRoles') and asks_clearance:
        return 'posting requires security clearance and the profile excludes clearance roles'
    return ''


def soft_flags(job: dict, loaded: dict) -> list[dict]:
    profile = loaded.get('profile') or {}
    flags = []
    floor = profile.get('softSalaryFloor')
    maximum = job.get('salary_max') if 'salary_max' in job else job.get('salaryMax')
    if floor not in (None, '') and maximum is not None and float(maximum) < float(floor):
        flags.append({'kind': 'salary_below_preference',
                      'detail': f"Posted maximum {maximum:g} is below your preferred floor {float(floor):g}."})
    text = _norm(f"{job.get('description') or ''} {job.get('title') or ''}")
    for preference in (profile.get('softPreferences') or '').split(','):
        preference = _norm(preference)
        if preference and preference not in text:
            flags.append({'kind': 'soft_preference_missing',
                          'detail': f"Posting does not mention your preference: {preference}."})
    return flags[:4]
