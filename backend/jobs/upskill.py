"""Local skill-gap heatmap with optional, source-linked learning resources."""
from __future__ import annotations

from collections import defaultdict
import time

from backend.jobs import keywords, profile as profile_mod
from backend.research import web

DAY = 86400


def heatmap(db, *, job_ids: list[str] | None = None, days: int = 90,
            limit: int = 20, now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    loaded = profile_mod.load_profile(db)
    profile_text = profile_mod.profile_text(loaded)
    vocabulary = keywords.build_vocabulary(profile_mod.skill_names(loaded))
    params: list = []
    where = 'dismissed=0 AND description<>?'
    params.append('')
    if job_ids:
        safe_ids = [str(value) for value in job_ids[:200] if value]
        if safe_ids:
            where += f" AND id IN ({','.join('?' for _ in safe_ids)})"
            params.extend(safe_ids)
    else:
        where += ' AND created_at>=?'
        params.append(now - max(1, min(days, 3650)) * DAY)
    rows = db.execute(f'SELECT id, title, company, description FROM jobs WHERE {where}', params).fetchall()
    have = set(keywords.extract_terms(profile_text, vocabulary))
    postings: dict[str, int] = defaultdict(int)
    mentions: dict[str, int] = defaultdict(int)
    examples: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        wanted = keywords.extract_terms(row['description'], vocabulary)
        for term, count in wanted.items():
            if term in have:
                continue
            postings[term] += 1
            mentions[term] += count
            if len(examples[term]) < 3:
                examples[term].append({'id': row['id'], 'title': row['title'],
                                       'company': row['company']})
    total = len(rows)
    skills = []
    for term in postings:
        frequency = postings[term] / total if total else 0
        centrality = min(1.0, frequency * 0.8 + min(mentions[term] / max(postings[term], 1), 5) / 25)
        skills.append({
            'term': term, 'postings': postings[term], 'ofPostings': total,
            'mentions': mentions[term], 'centrality': round(centrality, 3),
            # A deliberately rough orientation estimate, not a promise. It
            # scales with demand because broadly central skills need more than
            # a one-hour syntax tour to become interview-usable.
            'estimatedHours': round(8 + 32 * centrality),
            'examples': examples[term], 'resources': [],
        })
    skills.sort(key=lambda item: (-item['postings'], -item['mentions'], item['term']))
    return {'postings': total, 'skills': skills[:max(1, min(limit, 50))],
            'generatedAt': now, 'resourcesAvailable': web.is_search_configured()}


def enrich(plan: dict, *, resource_skills: int = 5) -> dict:
    for skill in plan.get('skills', [])[:max(0, min(resource_skills, 10))]:
        try:
            results = web.web_search(
                f'{skill["term"]} official tutorial learning guide', limit=3
            )
        except web.SearchUnavailable as exc:
            plan['resourceError'] = str(exc)
            break
        skill['resources'] = [
            {'title': row.get('title') or row['url'], 'url': row['url'],
             'snippet': row.get('snippet') or '', 'verifiedBy': 'configured web search'}
            for row in results if row.get('url')
        ]
    return plan
