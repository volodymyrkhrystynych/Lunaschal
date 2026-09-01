from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET

MAX_SCORE_BYTES = 12 * 1024 * 1024


class ScoreImportError(ValueError):
    pass


def normalize_score(data: bytes, filename: str) -> bytes:
    if not data:
        raise ScoreImportError('The score file is empty.')
    if len(data) > MAX_SCORE_BYTES:
        raise ScoreImportError('The score is larger than 12 MB.')
    suffix = filename.lower().rsplit('.', 1)[-1] if '.' in filename else ''
    if suffix == 'mxl':
        data = _extract_mxl(data)
    elif suffix not in {'xml', 'musicxml'}:
        raise ScoreImportError('Import a MusicXML, XML, or MXL score.')
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ScoreImportError('The file is not valid MusicXML.') from exc
    tag = root.tag.rsplit('}', 1)[-1]
    if tag not in {'score-partwise', 'score-timewise'}:
        raise ScoreImportError('The XML file is not a MusicXML score.')
    return data


def _extract_mxl(data: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            try:
                container = ET.fromstring(archive.read('META-INF/container.xml'))
            except (KeyError, ET.ParseError) as exc:
                raise ScoreImportError('The MXL archive has no valid score manifest.') from exc
            rootfile = next(
                (node for node in container.iter() if node.tag.rsplit('}', 1)[-1] == 'rootfile'),
                None,
            )
            path = rootfile.attrib.get('full-path') if rootfile is not None else None
            if not path or path.startswith('/') or '..' in path.split('/'):
                raise ScoreImportError('The MXL archive has an unsafe score path.')
            if archive.getinfo(path).file_size > MAX_SCORE_BYTES:
                raise ScoreImportError('The score inside the MXL archive is larger than 12 MB.')
            return archive.read(path)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ScoreImportError('The file is not a valid compressed MusicXML score.') from exc


def score_metadata(data: bytes, fallback_title: str) -> tuple[str, str | None]:
    root = ET.fromstring(data)
    title = _first_text(root, 'work-title') or _first_text(root, 'movement-title')
    composer = next(
        (
            (node.text or '').strip()
            for node in root.iter()
            if node.tag.rsplit('}', 1)[-1] == 'creator'
            and node.attrib.get('type') == 'composer'
            and (node.text or '').strip()
        ),
        None,
    )
    return title or fallback_title, composer


def _first_text(root: ET.Element, name: str) -> str | None:
    return next(
        (
            (node.text or '').strip()
            for node in root.iter()
            if node.tag.rsplit('}', 1)[-1] == name and (node.text or '').strip()
        ),
        None,
    )
