"""Hard job gates and non-rejecting soft preference annotations."""
import re

from backend.jobs import distance


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

    # Where the work is, decided in one place. This replaced two gates that
    # both read the location as *text*: `remoteOnly`, which searched the body
    # for "remote"/"on site" phrases, and `allowedLocations`, which substring
    # matched a comma-separated list against the location string. Neither could
    # tell that "Mississauga" is 24 km away or that "Bengaluru, India" is not,
    # so both were guesses about geography made with string operations.
    #
    # `distance.verdict` is three-valued on purpose and only `out_of_range`
    # rejects: a posting the gazetteer could not place is missing information,
    # not a job that is far away, and rejecting on silence would hide the ~75
    # rows that say nothing more than "Canada" or "N/A". Fully remote is exempt
    # inside `verdict`, which reads the structured `remote` + `work_location`
    # pair rather than searching prose for the word.
    max_km = profile.get('maxDistanceKm')
    if max_km not in (None, '') and distance.verdict(job, float(max_km)) == 'out_of_range':
        return (f"location {job.get('location')} is beyond your "
                f"{float(max_km):g} km radius")

    body = _norm(f"{job.get('title') or ''} {job.get('location') or ''} {job.get('description') or ''}")

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
