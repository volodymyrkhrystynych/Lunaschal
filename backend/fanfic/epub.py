"""EPUB import via stdlib zipfile + ElementTree: container.xml -> OPF
(metadata, manifest, spine) -> spine XHTML documents. Images referenced by
chapters are copied out of the archive into the fic's images dir and srcs
rewritten before sanitization."""

import hashlib
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from io import BytesIO

from bs4 import BeautifulSoup

from backend.fanfic import storage
from backend.fanfic.sanitize import sanitize_chapter_html

_NS = {
    'cnt': 'urn:oasis:names:tc:opendocument:xmlns:container',
    'opf': 'http://www.idpf.org/2007/opf',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'ncx': 'http://www.daisy.org/z3986/2005/ncx/',
    'xhtml': 'http://www.w3.org/1999/xhtml',
}

_IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.avif'}


class EpubError(ValueError):
    pass


@dataclass
class ImportedBook:
    title: str
    author: str | None
    chapters: list[tuple[str, str]]  # (title, sanitized_html)
    cover_path: str | None = None
    description: str | None = None


@dataclass
class _Opf:
    dir: str
    title: str
    author: str | None
    description: str | None
    manifest: dict[str, dict] = field(default_factory=dict)  # id -> {href, media_type, properties}
    spine: list[str] = field(default_factory=list)  # item ids in order
    cover_id: str | None = None
    nav_href: str | None = None
    ncx_href: str | None = None


def _opf_path(zf: zipfile.ZipFile) -> str:
    root = ET.fromstring(zf.read('META-INF/container.xml'))
    rootfile = root.find('.//cnt:rootfile', _NS)
    if rootfile is None or not rootfile.get('full-path'):
        raise EpubError('container.xml has no rootfile')
    return rootfile.get('full-path')


def _parse_opf(zf: zipfile.ZipFile, path: str) -> _Opf:
    root = ET.fromstring(zf.read(path))
    title_el = root.find('.//dc:title', _NS)
    author_el = root.find('.//dc:creator', _NS)
    desc_el = root.find('.//dc:description', _NS)
    opf = _Opf(
        dir=posixpath.dirname(path),
        title=(title_el.text or '').strip() if title_el is not None else '',
        author=(author_el.text or '').strip() or None if author_el is not None else None,
        description=(desc_el.text or '').strip() or None if desc_el is not None else None,
    )
    for item in root.findall('.//opf:manifest/opf:item', _NS):
        item_id, href = item.get('id'), item.get('href')
        if not item_id or not href:
            continue
        props = (item.get('properties') or '').split()
        opf.manifest[item_id] = {
            'href': posixpath.normpath(posixpath.join(opf.dir, href)),
            'media_type': item.get('media-type', ''),
            'properties': props,
        }
        if 'cover-image' in props:
            opf.cover_id = item_id
        if 'nav' in props:
            opf.nav_href = opf.manifest[item_id]['href']
        if item.get('media-type') == 'application/x-dtbncx+xml':
            opf.ncx_href = opf.manifest[item_id]['href']
    for itemref in root.findall('.//opf:spine/opf:itemref', _NS):
        idref = itemref.get('idref')
        if idref in opf.manifest:
            opf.spine.append(idref)
    if opf.cover_id is None:
        meta = root.find('.//opf:metadata/opf:meta[@name="cover"]', _NS)
        if meta is not None and meta.get('content') in opf.manifest:
            opf.cover_id = meta.get('content')
    return opf


def _toc_titles(zf: zipfile.ZipFile, opf: _Opf) -> dict[str, str]:
    """href (without fragment) -> title, from the EPUB3 nav doc or EPUB2 NCX."""
    titles: dict[str, str] = {}
    if opf.nav_href and opf.nav_href in zf.namelist():
        soup = BeautifulSoup(zf.read(opf.nav_href), 'html.parser')
        nav_dir = posixpath.dirname(opf.nav_href)
        for a in soup.select('nav a[href]'):
            href = posixpath.normpath(posixpath.join(nav_dir, a['href'].split('#')[0]))
            titles.setdefault(href, a.get_text(strip=True))
    elif opf.ncx_href and opf.ncx_href in zf.namelist():
        root = ET.fromstring(zf.read(opf.ncx_href))
        ncx_dir = posixpath.dirname(opf.ncx_href)
        for point in root.findall('.//ncx:navPoint', _NS):
            label = point.find('.//ncx:text', _NS)
            content = point.find('ncx:content', _NS)
            if label is None or content is None or not content.get('src'):
                continue
            href = posixpath.normpath(posixpath.join(ncx_dir, content.get('src').split('#')[0]))
            titles.setdefault(href, (label.text or '').strip())
    return titles


def _extract_image(zf: zipfile.ZipFile, zip_href: str, fic_id: str,
                   written: dict[str, str]) -> str | None:
    """Copy one image out of the zip; returns its local filename."""
    if zip_href in written:
        return written[zip_href]
    if zip_href not in zf.namelist():
        return None
    ext = posixpath.splitext(zip_href)[1].lower()
    if ext not in _IMAGE_EXTS:
        return None
    img_dir = storage.images_dir(fic_id)
    if img_dir is None:
        return None
    img_dir.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(zip_href.encode()).hexdigest()[:16] + ext
    (img_dir / name).write_bytes(zf.read(zip_href))
    written[zip_href] = name
    return name


def _chapter_html(zf: zipfile.ZipFile, doc_href: str, fic_id: str,
                  written: dict[str, str]) -> tuple[str, str | None]:
    """Returns (sanitized body html, fallback title from headings)."""
    soup = BeautifulSoup(zf.read(doc_href), 'html.parser')
    doc_dir = posixpath.dirname(doc_href)
    for img in soup.find_all(['img', 'image']):
        src = img.get('src') or img.get('{http://www.w3.org/1999/xlink}href') or img.get('xlink:href')
        if not src or src.startswith(('http://', 'https://', 'data:')):
            continue
        zip_href = posixpath.normpath(posixpath.join(doc_dir, src))
        name = _extract_image(zf, zip_href, fic_id, written)
        if name:
            img.name = 'img'
            img['src'] = f'/api/fanfic/{fic_id}/images/{name}'
    body = soup.find('body')
    html = body.decode_contents() if body else str(soup)
    heading = soup.find(['h1', 'h2', 'h3'])
    fallback = heading.get_text(strip=True) if heading else None
    return sanitize_chapter_html(html), fallback


def import_epub(data: bytes, fic_id: str, filename: str = '') -> ImportedBook:
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile:
        raise EpubError('Not a valid EPUB (zip) file')
    with zf:
        opf = _parse_opf(zf, _opf_path(zf))
        toc = _toc_titles(zf, opf)
        written: dict[str, str] = {}

        cover_name = None
        if opf.cover_id:
            cover_name = _extract_image(zf, opf.manifest[opf.cover_id]['href'], fic_id, written)

        chapters: list[tuple[str, str]] = []
        for item_id in opf.spine:
            item = opf.manifest[item_id]
            if item['href'] not in zf.namelist():
                continue
            html, fallback = _chapter_html(zf, item['href'], fic_id, written)
            if not re.search(r'\S', BeautifulSoup(html, 'html.parser').get_text()):
                if f'/api/fanfic/{fic_id}/images/' not in html:
                    continue  # skip empty filler pages
            title = toc.get(item['href']) or fallback or f'Chapter {len(chapters) + 1}'
            chapters.append((title, html))

        if not chapters:
            raise EpubError('No readable chapters found in the EPUB')

        title = opf.title or posixpath.splitext(filename)[0] or 'Untitled'
        return ImportedBook(title=title, author=opf.author, chapters=chapters,
                            cover_path=cover_name, description=opf.description)
