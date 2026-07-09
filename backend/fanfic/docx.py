"""DOCX import via mammoth: converts to HTML with an image handler that
writes embedded images into the fic's images dir. If the document has two or
more <h1> headings it is split into chapters at those boundaries."""

import hashlib
import posixpath
from io import BytesIO

from bs4 import BeautifulSoup

from backend.fanfic import storage
from backend.fanfic.epub import ImportedBook
from backend.fanfic.sanitize import sanitize_chapter_html

_CT_EXT = {
    'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif',
    'image/webp': '.webp', 'image/bmp': '.bmp', 'image/svg+xml': '.svg',
}


def _image_writer(fic_id: str):
    import mammoth
    counter = {'n': 0}

    def convert(image):
        img_dir = storage.images_dir(fic_id)
        if img_dir is None:
            return {}
        img_dir.mkdir(parents=True, exist_ok=True)
        with image.open() as f:
            data = f.read()
        counter['n'] += 1
        ext = _CT_EXT.get((image.content_type or '').split(';')[0], '.img')
        name = hashlib.sha1(data).hexdigest()[:16] + ext
        (img_dir / name).write_bytes(data)
        return {'src': f'/api/fanfic/{fic_id}/images/{name}'}

    return mammoth.images.img_element(convert)


def _split_on_h1(html: str) -> list[tuple[str | None, str]]:
    soup = BeautifulSoup(html, 'html.parser')
    h1s = soup.find_all('h1', recursive=False)
    if len(h1s) < 2:
        return [(None, html)]
    chapters: list[tuple[str | None, str]] = []
    title: str | None = None
    parts: list[str] = []
    for node in soup.contents:
        if getattr(node, 'name', None) == 'h1':
            if parts:
                chapters.append((title, ''.join(parts)))
            title = node.get_text(strip=True)
            parts = []
        else:
            parts.append(str(node))
    chapters.append((title, ''.join(parts)))
    # Drop a leading preamble that is only whitespace
    if chapters and chapters[0][0] is None and not chapters[0][1].strip():
        chapters = chapters[1:]
    return chapters


def import_docx(data: bytes, fic_id: str, filename: str = '') -> ImportedBook:
    import mammoth
    result = mammoth.convert_to_html(BytesIO(data), convert_image=_image_writer(fic_id))
    title = posixpath.splitext(filename)[0] or 'Untitled'
    raw_chapters = _split_on_h1(result.value)
    chapters: list[tuple[str, str]] = []
    for i, (chapter_title, chunk) in enumerate(raw_chapters):
        if not chapter_title:
            chapter_title = title if len(raw_chapters) == 1 else f'Chapter {i + 1}'
        chapters.append((chapter_title, sanitize_chapter_html(chunk)))
    return ImportedBook(title=title, author=None, chapters=chapters)
