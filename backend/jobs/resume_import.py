"""An existing resume → the structured profile everything else reads.

This exists because the profile is the root of the whole feature and the only
way to create one was to type it. Nothing works before it: `tailor.py` has no
bullets to select, and `keywords.keyword_report` has no vocabulary, so every
posting scores NULL and the feed cannot sort.

**The model never writes bullet text.** The document is split into numbered
lines and the schema's `bulletIndexes` is bounded to that list, the same
`tailor.build_schema` trick — llama-server compiles the bound into a GBNF
grammar, so an out-of-range index cannot be decoded, and there is no field in
which prose could be returned at all. Bullet text is then reconstructed
verbatim from the source lines.

That bound matters more here than in tailoring. A tailored bullet is reviewed
once and sent; an imported bullet becomes `profile_bullets`, which every future
resume is generated from and which the anti-fabrication guarantee treats as
fact. A quiet rewording at import would be baked in permanently.

Company, title, dates and contact details are ordinary strings. They cannot be
index-addressed — a resume line is routinely `Acme Inc · Senior Engineer ·
2021–Present`, three fields in one line — and they are short enough that a
wrong one is obvious in the review step.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from backend.ai.llm import chat_json
from backend.ai.provider import is_ai_configured

logger = logging.getLogger(__name__)

MAX_LINES = 400
MAX_LINE_CHARS = 600
MAX_TEXT_CHARS = 40000
MAX_ROLES = 20
MAX_BULLETS_PER_ROLE = 20
MAX_SKILLS = 60
MAX_EDUCATION = 10
MAX_TOKENS = 3072

# Characters resumes use to fake a list when the author never applied Word's
# list style. Common enough that ignoring them would mislabel most documents.
_BULLET_PREFIX = re.compile(r'^\s*[•·▪◦‣∙*\-–—]\s+')


@dataclass
class Line:
    """One line of the source document, with what little structure survived."""
    index: int
    text: str
    is_list: bool = False
    is_heading: bool = False

    @property
    def looks_like_bullet(self) -> bool:
        return self.is_list or bool(_BULLET_PREFIX.match(self.text))


class _LineExtractor(HTMLParser):
    """mammoth's HTML → lines, remembering list and heading membership.

    Uses the stdlib parser rather than BeautifulSoup because the only thing
    needed is "which tag am I inside", and mammoth's output is machine-made
    and well-formed.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines: list[tuple[str, bool, bool]] = []
        self._buffer: list[str] = []
        self._list_depth = 0
        self._heading = False

    def handle_starttag(self, tag, attrs):
        if tag in ('ul', 'ol'):
            self._list_depth += 1
        elif tag == 'li':
            self._flush()
        elif tag in ('p', 'div', 'tr', 'table'):
            self._flush()
        elif tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self._flush()
            self._heading = True
        elif tag == 'br':
            self._flush()

    def handle_endtag(self, tag):
        if tag in ('ul', 'ol'):
            self._list_depth = max(0, self._list_depth - 1)
        elif tag in ('li', 'p', 'div', 'tr', 'table'):
            self._flush()
        elif tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self._flush()
            self._heading = False

    def handle_data(self, data):
        self._buffer.append(data)

    def _flush(self):
        text = ' '.join(''.join(self._buffer).split())
        self._buffer = []
        if text:
            self.lines.append((text, self._list_depth > 0, self._heading))

    def close(self):
        super().close()
        self._flush()


def extract_lines(text: str = '', data: bytes | None = None,
                  filename: str = '') -> list[Line]:
    """The document as numbered lines. Accepts a .docx or plain text.

    `.docx` goes through mammoth — already a dependency, used by
    `backend/fanfic/docx.py` — because it maps Word's list paragraphs onto
    `<li>`, which is the one piece of structure worth having: it separates
    accomplishments from headings without guessing.
    """
    if data is not None:
        if not filename.lower().endswith('.docx'):
            raise ValueError('Only .docx files can be read — paste the text instead.')
        import mammoth
        from io import BytesIO
        try:
            html = mammoth.convert_to_html(BytesIO(data)).value
        except Exception as e:
            raise ValueError(f'Could not read that .docx: {e}') from e
        parser = _LineExtractor()
        parser.feed(html)
        parser.close()
        raw = parser.lines
    else:
        raw = [
            (' '.join(part.split()), False, False)
            for part in (text or '')[:MAX_TEXT_CHARS].split('\n')
        ]

    lines: list[Line] = []
    for content, is_list, is_heading in raw:
        content = content.strip()
        if not content:
            continue
        lines.append(Line(
            index=len(lines),
            text=content[:MAX_LINE_CHARS],
            is_list=is_list,
            is_heading=is_heading,
        ))
        if len(lines) >= MAX_LINES:
            break
    return lines


SYSTEM = """You are reading someone's existing resume and sorting it into \
structured fields. You are transcribing, not writing.

Every line of the document is numbered. For accomplishments you return the \
LINE NUMBERS only — never the text — because the stored text must be exactly \
what the document said.

- Group accomplishment lines under the role they belong to. A line that is a \
section heading ("EXPERIENCE", "SKILLS") is not an accomplishment.
- company / title / startLabel / endLabel are transcribed as they appear. \
Leave a field empty rather than inferring it; dates stay as written \
("2021", "Mar 2021", "Present").
- skills: individual technology and tool names, split out of whatever list \
they were written in. Not sentences.
- Use each line number at most once, under one role only."""


def build_schema(line_count: int) -> dict:
    """The import schema with the line bound applied.

    `bulletIndexes` is the whole point: the model can say *which* lines are
    accomplishments and cannot say what they contain.
    """
    last = max(line_count - 1, 0)
    index = {'type': 'integer', 'minimum': 0, 'maximum': last}

    return {
        'type': 'object',
        'properties': {
            'contact': {
                'type': 'object',
                'properties': {
                    'fullName': {'type': 'string'},
                    'email': {'type': 'string'},
                    'phone': {'type': 'string'},
                    'location': {'type': 'string'},
                    'headline': {'type': 'string'},
                },
            },
            'roles': {
                'type': 'array',
                'maxItems': MAX_ROLES,
                'items': {
                    'type': 'object',
                    'properties': {
                        'company': {'type': 'string'},
                        'title': {'type': 'string'},
                        'location': {'type': 'string'},
                        'startLabel': {'type': 'string'},
                        'endLabel': {'type': 'string'},
                        'bulletIndexes': {
                            'type': 'array',
                            'maxItems': MAX_BULLETS_PER_ROLE,
                            'items': index,
                        },
                    },
                    'required': ['company', 'title', 'bulletIndexes'],
                },
            },
            'skills': {
                'type': 'array',
                'maxItems': MAX_SKILLS,
                'items': {'type': 'string'},
            },
            'education': {
                'type': 'array',
                'maxItems': MAX_EDUCATION,
                'items': {
                    'type': 'object',
                    'properties': {
                        'institution': {'type': 'string'},
                        'credential': {'type': 'string'},
                        'field': {'type': 'string'},
                        'startLabel': {'type': 'string'},
                        'endLabel': {'type': 'string'},
                    },
                    'required': ['institution'],
                },
            },
        },
        'required': ['roles', 'skills'],
    }


def build_prompt(lines: list[Line]) -> str:
    numbered = []
    for line in lines:
        marker = '•' if line.looks_like_bullet else ('#' if line.is_heading else ' ')
        numbered.append(f'[{line.index}] {marker} {line.text}')
    return (
        'Here is the resume, one numbered line per row. A `•` marks a line '
        'that looked like a list item and `#` a line that looked like a '
        'heading — both are hints from the document\'s formatting, not '
        'instructions.\n\n' + '\n'.join(numbered)
    )


def _clean(value, limit: int = 200) -> str:
    return ' '.join(str(value or '').split())[:limit]


def clamp(result: dict, lines: list[Line]) -> dict:
    """Resolve the model's answer against the real document.

    The grammar should make the index bound redundant; it is re-applied anyway,
    because what comes out of here becomes the user's professional history and
    every future resume is generated from it.
    """
    by_index = {line.index: line for line in lines}
    used: set[int] = set()

    roles = []
    for item in (result.get('roles') or [])[:MAX_ROLES]:
        if not isinstance(item, dict):
            continue
        company = _clean(item.get('company'))
        title = _clean(item.get('title'))
        if not company and not title:
            # A role identified by neither is not a role; it is usually the
            # model trying to make a section heading into one.
            continue

        bullets = []
        for raw_index in (item.get('bulletIndexes') or []):
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            line = by_index.get(index)
            # `used` is global across roles on purpose: the same line appearing
            # under two jobs is a duplicated accomplishment, which reads as
            # padding on the rendered resume.
            if line is None or index in used:
                continue
            used.add(index)
            text = _BULLET_PREFIX.sub('', line.text).strip()
            if text:
                bullets.append({'index': index, 'text': text})
            if len(bullets) >= MAX_BULLETS_PER_ROLE:
                break

        roles.append({
            'company': company,
            'title': title,
            'location': _clean(item.get('location')),
            'startLabel': _clean(item.get('startLabel'), 40),
            'endLabel': _clean(item.get('endLabel'), 40),
            'bullets': bullets,
        })

    skills = []
    seen_skills = set()
    for raw in (result.get('skills') or []):
        name = _clean(raw, 60)
        key = name.casefold()
        if name and key not in seen_skills:
            seen_skills.add(key)
            skills.append(name)
        if len(skills) >= MAX_SKILLS:
            break

    education = []
    for item in (result.get('education') or [])[:MAX_EDUCATION]:
        if not isinstance(item, dict):
            continue
        institution = _clean(item.get('institution'))
        credential = _clean(item.get('credential'))
        if not institution and not credential:
            continue
        education.append({
            'institution': institution,
            'credential': credential,
            'field': _clean(item.get('field')),
            'startLabel': _clean(item.get('startLabel'), 40),
            'endLabel': _clean(item.get('endLabel'), 40),
        })

    contact_in = result.get('contact') if isinstance(result.get('contact'), dict) else {}
    contact = {
        'fullName': _clean(contact_in.get('fullName'), 120),
        'email': _clean(contact_in.get('email'), 200),
        'phone': _clean(contact_in.get('phone'), 60),
        'location': _clean(contact_in.get('location'), 120),
        'headline': _clean(contact_in.get('headline'), 200),
    }

    return {
        'contact': contact,
        'roles': roles,
        'skills': skills,
        'education': education,
        'lineCount': len(lines),
        'unusedLines': [
            {'index': line.index, 'text': line.text}
            for line in lines if line.index not in used
        ],
    }


def import_resume(lines: list[Line]) -> dict | None:
    """One resume → a structured preview. None when the model is unavailable.

    None rather than a partial guess, the `tailor_resume` contract: an import
    that silently produced an empty profile would look identical to a resume
    the parser could not understand, and the user would go and type it all in
    for no reason.
    """
    if not lines or not is_ai_configured():
        return None

    try:
        result = chat_json(
            build_prompt(lines),
            system=SYSTEM,
            schema=build_schema(len(lines)),
            max_tokens=MAX_TOKENS,
        )
    except Exception as e:
        logger.warning('Resume import failed: %s', e)
        return None

    if not isinstance(result, dict):
        return None
    return clamp(result, lines)
