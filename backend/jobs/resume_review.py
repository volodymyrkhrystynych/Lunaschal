"""Local ATS-style review of rendered resumes, including the PDF text layer."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ACTION_VERBS = frozenset({
    'built', 'created', 'designed', 'developed', 'delivered', 'implemented',
    'improved', 'increased', 'reduced', 'cut', 'led', 'launched', 'managed',
    'migrated', 'optimized', 'automated', 'scaled', 'shipped', 'owned',
    'resolved', 'recovered', 'introduced', 'established', 'mentored',
})


def extract_pdf_text(path: Path) -> str | None:
    executable = shutil.which('pdftotext')
    if not executable or not path.exists():
        return None
    try:
        result = subprocess.run(
            [executable, '-layout', str(path), '-'], capture_output=True,
            text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _norm(value: str) -> str:
    return re.sub(r'\s+', ' ', (value or '').lower()).strip()


def review(loaded: dict, content: dict, *, pdf_path: Path | None = None) -> dict:
    bullets = [b.get('text') or '' for b in content.get('selectedBullets') or []]
    starts = [re.findall(r"[a-z]+", bullet.lower())[:1] for bullet in bullets]
    action_count = sum(1 for words in starts if words and words[0] in ACTION_VERBS)
    quantified = sum(1 for bullet in bullets if re.search(r'\b\d[\d,.]*\b|\d+\s*%', bullet))
    total = len(bullets)
    text = extract_pdf_text(pdf_path) if pdf_path else None
    profile = loaded.get('profile') or {}
    contacts = [profile.get('fullName'), profile.get('email'), profile.get('phone')]
    contact_checks = {
        key: bool(value and _norm(str(value)) in _norm(text or ''))
        for key, value in zip(('name', 'email', 'phone'), contacts) if value
    }
    keywords = (content.get('keywords') or {}).get('matched') or []
    extracted_keywords = [term for term in keywords if _norm(term) in _norm(text or '')]
    expected_sections = []
    if content.get('summary') or profile.get('summary'): expected_sections.append('summary')
    if loaded.get('roles'): expected_sections.append('experience')
    if loaded.get('skills'): expected_sections.append('skills')
    if loaded.get('education'): expected_sections.append('education')
    positions = [_norm(text or '').find(section) for section in expected_sections]
    reading_order = bool(text) and all(p >= 0 for p in positions) and positions == sorted(positions)
    parseable = text is not None and len(_norm(text)) >= 100 and '\ufffd' not in text
    issues = []
    if pdf_path and text is None: issues.append('PDF text extraction is unavailable.')
    if text is not None and not parseable: issues.append('The PDF text layer is sparse or corrupted.')
    missing_contacts = [k for k, ok in contact_checks.items() if not ok]
    if missing_contacts: issues.append('Missing from PDF text: ' + ', '.join(missing_contacts) + '.')
    if text is not None and not reading_order: issues.append('Expected sections do not extract in reading order.')
    missing_keywords = [term for term in keywords if term not in extracted_keywords]
    if missing_keywords: issues.append('Matched keywords missing from PDF text: ' + ', '.join(missing_keywords) + '.')
    return {
        'pdfChecked': text is not None,
        'parseable': parseable if pdf_path else None,
        'contactChecks': contact_checks,
        'readingOrder': reading_order if text is not None else None,
        'keywordChecks': {'expected': keywords, 'extracted': extracted_keywords,
                          'coverage': round(len(extracted_keywords)/len(keywords), 3) if keywords else 1},
        'metrics': {
            'bulletCount': total,
            'actionVerbDensity': round(action_count / total, 3) if total else 0,
            'quantifiedImpactDensity': round(quantified / total, 3) if total else 0,
            'sectionSanity': all(p >= 0 for p in positions) if text is not None else None,
        },
        'issues': issues,
    }
