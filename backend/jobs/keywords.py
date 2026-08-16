"""Deterministic keyword gap between a posting and the profile.

No model. The point of this module is to be *trustworthy* about what a posting
asks for and what the profile can honestly claim, so that tailoring never has
to guess and the user is never quietly given credit for a skill they do not
have. `backend/research/repo_facts.py` makes the same trade for the same
reason: a fact the model could have invented is not evidence.

Matching is vocabulary-driven, so there are no false positives — a term is only
reported if it is a term we know. The vocabulary is BASE_TERMS plus the user's
own skill names, which means it grows with the profile: list Elixir once and
every future posting is checked for it.

`missing` is ordered by how often the posting mentions a term, so the gaps the
employer emphasised come first rather than whichever happened to sort early.
"""
import re
from dataclasses import dataclass, field

# Terms worth mirroring in a resume when they are genuinely true. Kept to
# concrete, checkable technologies and practices — no soft skills, because
# "communication" appears in every posting and matching it means nothing.
BASE_TERMS = frozenset({
    # languages
    'python', 'javascript', 'typescript', 'java', 'kotlin', 'swift', 'go',
    'golang', 'rust', 'ruby', 'php', 'c', 'c++', 'c#', 'scala', 'elixir',
    'erlang', 'haskell', 'clojure', 'perl', 'r', 'matlab', 'bash', 'shell',
    'sql', 'html', 'css', 'sass', 'lua', 'dart', 'objective-c', 'groovy',
    # frontend
    'react', 'react native', 'vue', 'angular', 'svelte', 'next.js', 'nuxt',
    'redux', 'tailwind', 'webpack', 'vite', 'jquery', 'bootstrap', 'graphql',
    'storybook', 'accessibility', 'wcag', 'responsive design',
    # backend / frameworks
    'node.js', 'express', 'django', 'flask', 'fastapi', 'rails', 'spring',
    'spring boot', 'laravel', '.net', 'asp.net', 'phoenix', 'gin', 'nestjs',
    'rest', 'grpc', 'websockets', 'microservices', 'api design',
    # data
    'postgresql', 'postgres', 'mysql', 'sqlite', 'mongodb', 'redis',
    'cassandra', 'dynamodb', 'elasticsearch', 'opensearch', 'neo4j',
    'snowflake', 'bigquery', 'redshift', 'databricks', 'clickhouse',
    'kafka', 'rabbitmq', 'airflow', 'dbt', 'spark', 'hadoop', 'flink',
    'etl', 'data warehouse', 'data modeling', 'pandas', 'numpy',
    # cloud / infra
    'aws', 'azure', 'gcp', 'google cloud', 'docker', 'kubernetes', 'k8s',
    'terraform', 'ansible', 'pulumi', 'helm', 'jenkins', 'github actions',
    'gitlab ci', 'circleci', 'ci/cd', 'linux', 'nginx', 'lambda', 's3',
    'ec2', 'cloudformation', 'datadog', 'prometheus', 'grafana', 'splunk',
    'observability', 'sre', 'devops', 'infrastructure as code',
    # ml
    'machine learning', 'deep learning', 'pytorch', 'tensorflow',
    'scikit-learn', 'nlp', 'computer vision', 'llm', 'transformers',
    'hugging face', 'mlops', 'rag', 'recommendation systems',
    # mobile
    'ios', 'android', 'flutter', 'xcode', 'swiftui', 'jetpack compose',
    # practice
    'agile', 'scrum', 'kanban', 'tdd', 'unit testing', 'integration testing',
    'code review', 'pair programming', 'git', 'jira', 'oauth', 'saml',
    'security', 'penetration testing', 'gdpr', 'soc 2', 'hipaa', 'pci',
    'a/b testing', 'analytics', 'sql tuning', 'performance optimization',
    'distributed systems', 'event driven', 'design patterns', 'uml',
    # business-side
    'salesforce', 'sap', 'excel', 'power bi', 'tableau', 'looker',
    'quickbooks', 'netsuite', 'workday', 'figma', 'sketch',
})


@dataclass
class KeywordReport:
    """What the posting asks for, split by whether the profile can back it."""
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        total = len(self.matched) + len(self.missing)
        return len(self.matched) / total if total else 0.0

    def to_dict(self) -> dict:
        return {
            'matched': self.matched,
            'missing': self.missing,
            'coverage': round(self.coverage, 3),
        }


def _normalize(text: str) -> str:
    """Lowercase with punctuation collapsed to spaces — except the characters
    that carry meaning inside a technology's name. Dropping them would fold
    'c++' into 'c' and 'ci/cd' into two unrelated words.
    """
    lowered = (text or '').lower()
    kept = re.sub(r'[^a-z0-9+#./\- ]+', ' ', lowered)
    return ' '.join(kept.split())


def _spans(haystack: str, term: str) -> list[tuple[int, int]]:
    """Where `term` appears in normalized text, on word boundaries."""
    pattern = r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])'
    return [m.span() for m in re.finditer(pattern, haystack)]


def build_vocabulary(profile_terms: list[str] | None = None) -> set[str]:
    """BASE_TERMS widened by whatever the user has actually listed."""
    vocab = set(BASE_TERMS)
    for term in profile_terms or []:
        normalized = _normalize(term)
        # Multi-word skills are fine; empty and single-character ones are noise.
        if len(normalized) >= 2:
            vocab.add(normalized)
    return vocab


def extract_terms(text: str, vocabulary: set[str]) -> dict[str, int]:
    """Vocabulary terms present in `text`, mapped to their mention count.

    Longest match wins over any span it covers. Word boundaries alone are not
    enough, because the characters that make 'c++' and 'ci/cd' distinct terms
    are the same characters a boundary treats as a word end — so 'c' matches
    inside 'c++', and '.net' inside 'asp.net'. Tightening the boundary instead
    would break the far more common case of a term ending a sentence
    ('We use Python.'). Subsumption handles both without a special case.
    """
    hay = _normalize(text)

    hits: list[tuple[int, int, str]] = []
    for term in vocabulary:
        hits.extend((start, end, term) for start, end in _spans(hay, term))

    # Longest first, so a span is only ever compared against ones at least as
    # long as itself.
    hits.sort(key=lambda h: h[1] - h[0], reverse=True)

    found: dict[str, int] = {}
    kept: list[tuple[int, int]] = []
    for start, end, term in hits:
        if any(k_start <= start and end <= k_end for k_start, k_end in kept):
            continue
        kept.append((start, end))
        found[term] = found.get(term, 0) + 1
    return found


def keyword_report(
    job_description: str,
    profile_text: str,
    profile_skills: list[str] | None = None,
) -> KeywordReport:
    """Which of the posting's terms the profile already evidences.

    `profile_text` should be everything the user has written about themselves —
    skills, bullets, summary, role titles — because a bullet describing a
    Kubernetes migration is proof of Kubernetes whether or not it was also
    typed into the skills list.
    """
    vocabulary = build_vocabulary(profile_skills)
    wanted = extract_terms(job_description, vocabulary)
    if not wanted:
        return KeywordReport()

    have = extract_terms(profile_text, vocabulary)

    matched = sorted(t for t in wanted if t in have)
    # Most-emphasised gaps first; alphabetical within a tier so the order is
    # stable across runs.
    missing = sorted(
        (t for t in wanted if t not in have),
        key=lambda t: (-wanted[t], t),
    )
    return KeywordReport(matched=matched, missing=missing)
