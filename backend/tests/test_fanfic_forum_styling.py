"""Forum posts keep what the forum shows: spoilers stay collapsible, quotes
lose their dead "Click to expand..." link, and colour/bold/font survive
(`xenforo.normalize_bbcode_html` → `sanitize_chapter_html`, joined in
`restyle.render_forum_post`)."""
from pathlib import Path

from bs4 import BeautifulSoup

from backend.fanfic import restyle, xenforo
from backend.fanfic.sanitize import sanitize_chapter_html

FIXTURES = Path(__file__).parent / 'fixtures' / 'fanfic'


def _styled_post_html() -> str:
    page = xenforo.parse_reader_page((FIXTURES / 'reader_styled.html').read_text())
    assert len(page.posts) == 1
    return page.posts[0].content_html


def _rendered() -> BeautifulSoup:
    return BeautifulSoup(restyle.render_forum_post(_styled_post_html()), 'html.parser')


def test_a_spoiler_becomes_details_with_its_title():
    soup = _rendered()
    outer = soup.find('details')
    assert outer is not None
    assert outer.find('summary').get_text() == 'Status Sheet'
    assert 'STR:' in outer.get_text()
    # the forum's toggle button is gone, not left behind as a text line
    assert 'Click to reveal' not in str(soup)
    assert 'Spoiler: Status Sheet' not in soup.get_text()


def test_nested_spoilers_stay_nested_and_an_untitled_one_says_spoiler():
    outer = _rendered().find('details')
    inner = outer.find('details')
    assert inner is not None and inner.find_parent('details') is outer
    assert inner.find('summary').get_text() == 'Spoiler'
    assert 'Hidden perk.' in inner.get_text()


def test_a_quote_keeps_its_attribution_and_loses_the_expand_link():
    soup = _rendered()
    quotes = soup.find_all('blockquote')
    assert [q.find('cite').get_text() for q in quotes] == ['Narrator said:', 'Bob said:']
    assert 'Click to expand' not in soup.get_text()
    # a quote inside a spoiler is the spoiler's content, not the spoiler body
    assert quotes[0].find_parent('details') is not None
    assert 'An earlier post.' in quotes[1].get_text()


def test_an_inline_spoiler_keeps_one_class_and_nothing_else():
    soup = _rendered()
    span = soup.find('span', class_='spoiler')
    assert span is not None and span.get_text() == 'the butler'
    assert span.attrs == {'class': ['spoiler']}


def test_colour_bold_and_font_survive():
    html = restyle.render_forum_post(_styled_post_html())
    assert '<b>Bold opening.</b>' in html
    assert 'color:#b30000' in html.replace(' ', '')
    assert 'Courier New' in html


def test_no_forum_class_survives_the_sanitizer():
    html = sanitize_chapter_html(
        '<div class="bbCodeBlock"><span class="spoiler other">a</span>'
        '<span class="bbCodeInlineSpoiler">b</span><p class="spoiler">c</p></div>')
    soup = BeautifulSoup(html, 'html.parser')
    classes = [c for t in soup.find_all(class_=True) for c in t.get('class')]
    assert classes == ['spoiler']


def test_normalizing_plain_markup_changes_nothing_visible():
    html = '<b>bold</b> <i>it</i> <span style="color: red">red</span><br>line'
    assert sanitize_chapter_html(xenforo.normalize_bbcode_html(html)) == sanitize_chapter_html(html)


def test_restyle_rewrites_only_chapters_whose_output_changed(client):
    from backend.db.connection import get_db
    db = get_db()
    now = 1700000000
    db.execute("INSERT INTO fics(id, title, source_type, created_at, updated_at)"
               " VALUES ('f1', 'Fic', 'xenforo', ?, ?)", (now, now))
    source = _styled_post_html()
    current = restyle.render_forum_post(source)
    rows = [
        # stored before spoilers were understood: stale HTML, raw source kept
        ('c1', 1, '<p>stale</p>', source),
        # already current
        ('c2', 2, current, source),
        # an upload: nothing to restyle from
        ('c3', 3, '<p>from an epub</p>', None),
    ]
    for cid, pos, html, src in rows:
        db.execute(
            'INSERT INTO fic_chapters(id, fic_id, position, title, content_html, content_text,'
            ' source_html, word_count, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (cid, 'f1', pos, f'Ch {pos}', html, 'x', src, 1, now, now))
    db.commit()

    assert restyle.restyle_chapters(db) == 1

    got = {r['id']: r for r in db.execute(
        'SELECT id, content_html, content_text, updated_at FROM fic_chapters')}
    assert got['c1']['content_html'] == current
    assert 'the butler' in got['c1']['content_text']
    assert got['c1']['updated_at'] > now
    assert got['c2']['updated_at'] == now
    assert got['c3']['content_html'] == '<p>from an epub</p>'
    assert restyle.restyle_chapters(db) == 0


def test_source_html_never_reaches_the_phone():
    from backend.mobile_sync import registry
    assert 'source_html' not in registry.COLLECTIONS['fic_chapters']


def test_a_fic_has_its_source_only_when_every_chapter_does(client):
    import json
    from backend.db.connection import get_db
    db = get_db()
    for fid, kind in (('f1', 'xenforo'), ('f2', 'epub')):
        db.execute('INSERT INTO fics(id, title, source_type, created_at, updated_at)'
                   " VALUES (?, 'Fic', ?, 1, 1)", (fid, kind))
        for n in (1, 2):
            db.execute(
                'INSERT INTO fic_chapters(id, fic_id, position, title, content_html, content_text,'
                " source_post_id, created_at, updated_at) VALUES (?,?,?,'C','x','x',?,1,1)",
                (f'{fid}c{n}', fid, n, str(n)))
    db.commit()

    def synced(fid):
        return json.loads(db.execute(
            "SELECT payload FROM mobile_sync_changes WHERE collection='fics' AND record_id=?"
            ' ORDER BY sequence DESC LIMIT 1', (fid,)).fetchone()[0])['has_source']

    def rest(fid):
        return client.get(f'/api/fanfic/{fid}').get_json()['hasSource']

    assert (synced('f1'), rest('f1')) == (0, False)
    assert (synced('f2'), rest('f2')) == (None, None)
    # Storing the source alone is enough to reach the phone.
    for whole, cid in ((0, 'f1c1'), (1, 'f1c2')):
        db.execute("UPDATE fic_chapters SET source_html='<b>x</b>' WHERE id=?", (cid,))
        db.commit()
        assert (synced('f1'), rest('f1')) == (whole, bool(whole))

    # A fic with nothing downloaded has nothing to show for it.
    db.execute("DELETE FROM fic_chapters WHERE fic_id='f1'")
    db.commit()
    assert (synced('f1'), rest('f1')) == (0, False)
