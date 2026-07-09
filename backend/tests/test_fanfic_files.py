"""EPUB/DOCX/PDF upload tests. The books are built in-test with zipfile so no
binary fixtures are committed."""
import io
import zipfile

import pytest


@pytest.fixture(autouse=True)
def fanfic_root(monkeypatch, tmp_path):
    monkeypatch.setenv('FANFIC_ROOT', str(tmp_path / 'fanfic'))
    return tmp_path / 'fanfic'


# --- builders ---

PNG = b'\x89PNG\r\n\x1a\nfake-image-bytes'

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>My Epub Story</dc:title>
    <dc:creator>Epub Author</dc:creator>
    <dc:description>An epub about zip files.</dc:description>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="c1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="cover" href="images/cover.png" media-type="image/png" properties="cover-image"/>
    <item id="pic" href="images/pic.png" media-type="image/png"/>
  </manifest>
  <spine>
    <itemref idref="c1"/>
    <itemref idref="c2"/>
  </spine>
</package>"""

NAV = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops"><ol>
  <li><a href="text/ch1.xhtml">The Beginning</a></li>
  <li><a href="text/ch2.xhtml">The End</a></li>
</ol></nav></body></html>"""

CH1 = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>The Beginning</h1><p>Once upon a time in a zip archive.</p>
<img src="../images/pic.png" alt="illustration"/>
<script>alert('epub xss')</script>
</body></html>"""

CH2 = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p>And they lived happily ever after.</p></body></html>"""


def build_epub() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('mimetype', 'application/epub+zip')
        zf.writestr('META-INF/container.xml', CONTAINER)
        zf.writestr('OEBPS/content.opf', OPF)
        zf.writestr('OEBPS/nav.xhtml', NAV)
        zf.writestr('OEBPS/text/ch1.xhtml', CH1)
        zf.writestr('OEBPS/text/ch2.xhtml', CH2)
        zf.writestr('OEBPS/images/cover.png', PNG)
        zf.writestr('OEBPS/images/pic.png', PNG)
    return buf.getvalue()


DOCX_DOCUMENT = """<?xml version="1.0"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Part One</w:t></w:r></w:p>
    <w:p><w:r><w:t>The first part of the story.</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Part Two</w:t></w:r></w:p>
    <w:p><w:r><w:t>The second part of the story.</w:t></w:r></w:p>
  </w:body>
</w:document>"""

DOCX_CONTENT_TYPES = """<?xml version="1.0"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""


def build_docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('[Content_Types].xml', DOCX_CONTENT_TYPES)
        zf.writestr('word/document.xml', DOCX_DOCUMENT)
    return buf.getvalue()


def upload(client, name: str, data: bytes):
    return client.post('/api/fanfic/upload', data={
        'file': (io.BytesIO(data), name),
    }, content_type='multipart/form-data')


# --- tests ---

def test_epub_upload(client, fanfic_root):
    resp = upload(client, 'my-story.epub', build_epub())
    assert resp.status_code == 201, resp.get_json()
    fic = resp.get_json()['fic']
    assert fic['title'] == 'My Epub Story'
    assert fic['author'] == 'Epub Author'
    assert fic['sourceType'] == 'epub'
    assert fic['chapterCount'] == 2
    assert fic['coverPath']

    fic_id = resp.get_json()['id']
    chapters = client.get(f'/api/fanfic/{fic_id}/chapters').get_json()
    assert [c['title'] for c in chapters] == ['The Beginning', 'The End']
    assert all(c['category'] == 'chapters' for c in chapters)

    ch1 = client.get(f"/api/fanfic/chapters/{chapters[0]['id']}").get_json()
    assert 'Once upon a time' in ch1['contentHtml']
    assert '<script' not in ch1['contentHtml']
    assert f'/api/fanfic/{fic_id}/images/' in ch1['contentHtml']

    # cover + illustration extracted from the zip and served
    import re
    m = re.search(rf'/api/fanfic/{fic_id}/images/([A-Za-z0-9._-]+)', ch1['contentHtml'])
    served = client.get(f'/api/fanfic/{fic_id}/images/{m.group(1)}')
    assert served.status_code == 200
    assert served.data == PNG


def test_docx_upload_splits_on_h1(client):
    resp = upload(client, 'two-parts.docx', build_docx())
    assert resp.status_code == 201, resp.get_json()
    fic = resp.get_json()['fic']
    assert fic['title'] == 'two-parts'
    assert fic['sourceType'] == 'docx'
    assert fic['chapterCount'] == 2

    chapters = client.get(f"/api/fanfic/{resp.get_json()['id']}/chapters").get_json()
    assert [c['title'] for c in chapters] == ['Part One', 'Part Two']
    ch2 = client.get(f"/api/fanfic/chapters/{chapters[1]['id']}").get_json()
    assert 'second part' in ch2['contentHtml']
    assert 'first part' not in ch2['contentHtml']


def test_pdf_upload_and_serving(client, fanfic_root):
    pdf_bytes = b'%PDF-1.4 fake pdf content'
    resp = upload(client, 'Some Fic.pdf', pdf_bytes)
    assert resp.status_code == 201
    body = resp.get_json()
    assert body['fic']['title'] == 'Some Fic'
    assert body['fic']['sourceType'] == 'pdf'
    assert body['fic']['chapterCount'] == 0
    assert client.get(f"/api/fanfic/{body['id']}/chapters").get_json() == []

    served = client.get(f"/api/fanfic/{body['id']}/pdf")
    assert served.status_code == 200
    assert served.mimetype == 'application/pdf'
    assert served.data == pdf_bytes


def test_bad_extension_rejected(client):
    resp = upload(client, 'story.txt', b'hello')
    assert resp.status_code == 400


def test_missing_file_rejected(client):
    resp = client.post('/api/fanfic/upload', data={}, content_type='multipart/form-data')
    assert resp.status_code == 400


def test_corrupt_epub_rolls_back(client):
    resp = upload(client, 'broken.epub', b'not a zip at all')
    assert resp.status_code == 422
    assert client.get('/api/fanfic').get_json() == []
