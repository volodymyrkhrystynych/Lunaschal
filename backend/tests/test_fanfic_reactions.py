"""The user's own forum reaction on each chapter, as proof it was read."""
import re

from backend.db.connection import get_db
from backend.fanfic.xenforo import parse_reader_page
from backend.tests.test_fanfic_import import THREAD, _import_fic, fake_net  # noqa: F401

READER_P1 = f'{THREAD}/reader?threadmark_category=1'


def _button(reaction: str | None) -> str:
    """XenForo 2's react button as the viewer sees it on someone else's post."""
    state = ' has-reaction' if reaction else ''
    text = reaction or 'Like'
    return (f'<footer class="message-footer"><div class="message-actionBar actionBar">'
            f'<a href="/posts/1/react?reaction_id=1" class="reaction reaction--small'
            f' actionBar-action actionBar-action--reaction{state}" data-reaction-id="1">'
            f'<i aria-hidden="true"></i><img class="reaction-sprite js-reaction" alt="{text}"'
            f' title="{text}"><span class="reaction-text js-reactionText"><bdi>{text}</bdi></span>'
            f'</a></div></footer>')


def _page(html: str, reactions: dict[str, str | None], logged_in=True) -> str:
    html = html.replace('<html>', f'<html data-logged-in="{"true" if logged_in else "false"}">', 1)

    def add(match):
        post_id = match.group(2)
        return match.group(1) + (_button(reactions[post_id]) if post_id in reactions else '')
    return re.sub(r'(data-content="post-(\d+)"\s*>)', add, html)


def _reactions(fic_id):
    rows = get_db().execute(
        'SELECT c.source_post_id, r.reaction, r.checked_at FROM fic_chapter_reactions r'
        ' JOIN fic_chapters c ON c.id = r.chapter_id WHERE r.fic_id=?', (fic_id,)).fetchall()
    return {r['source_post_id']: (r['reaction'], r['checked_at']) for r in rows}


# --- parser ---

def test_parser_reads_the_viewers_reaction_not_the_reactions_bar():
    html = ('<html data-logged-in="true"><body>'
            '<article class="message" data-content="post-1"><div class="bbWrapper">One</div>'
            + _button('Informative') +
            '<div class="reactionsBar"><a class="reactionsBar-link"><bdi>Someone</bdi> liked this</a></div>'
            '</article>'
            '<article class="message" data-content="post-2"><div class="bbWrapper">Two</div>'
            + _button(None) + '</article>'
            '<article class="message" data-content="post-3"><div class="bbWrapper">Mine</div></article>'
            '</body></html>')
    page = parse_reader_page(html)
    assert page.logged_in is True
    assert [(p.reaction_seen, p.my_reaction) for p in page.posts] == [
        (True, 'Informative'), (True, None), (False, None)]


def test_parser_names_a_reaction_from_its_sprite_when_the_text_is_missing():
    html = ('<html data-logged-in="true"><article class="message" data-content="post-1"><div class="bbWrapper">x</div>'
            '<a class="actionBar-action actionBar-action--reaction has-reaction">'
            '<img class="reaction-sprite" alt="Love" title="Love"></a></article></html>')
    assert parse_reader_page(html).posts[0].my_reaction == 'Love'


def test_a_page_without_the_logged_in_flag_is_not_logged_in():
    html = '<html><article class="message" data-content="post-1"><div class="bbWrapper">x</div></article></html>'
    assert parse_reader_page(html).logged_in is False


# --- download ---

def test_a_logged_in_import_records_each_chapters_reaction(client, fake_net):  # noqa: F811
    fake_net['pages'][READER_P1] = _page(fake_net['pages'][READER_P1], {'101': 'Like', '102': None})
    fic_id = _import_fic(client)
    found = _reactions(fic_id)
    assert found['101'][0] == 'Like'
    assert found['102'][0] is None and found['102'][1] > 0  # checked, not reacted
    assert '103' not in found  # never seen with a button: unknown


def test_a_logged_out_page_never_erases_proof(client, fake_net):  # noqa: F811
    original = fake_net['pages'][READER_P1]
    fake_net['pages'][READER_P1] = _page(original, {'101': 'Like'})
    fic_id = _import_fic(client)
    db = get_db()
    db.execute('UPDATE fic_chapter_reactions SET checked_at=5')
    db.commit()
    fake_net['pages'][READER_P1] = _page(original, {'101': None}, logged_in=False)
    assert client.post(f'/api/fanfic/{fic_id}/check-updates', json={'deep': True}).status_code == 202
    assert _reactions(fic_id)['101'] == ('Like', 5)


def test_a_deep_check_refreshes_reactions_without_rewriting_chapters(client, fake_net):  # noqa: F811
    original = fake_net['pages'][READER_P1]
    fake_net['pages'][READER_P1] = _page(original, {'101': None, '102': None})
    fic_id = _import_fic(client)
    db = get_db()
    db.execute('UPDATE fic_chapter_reactions SET checked_at=5')
    db.execute('UPDATE fic_chapters SET updated_at=7 WHERE fic_id=?', (fic_id,))
    db.commit()
    fake_net['pages'][READER_P1] = _page(original, {'101': 'Like', '102': None})
    assert client.post(f'/api/fanfic/{fic_id}/check-updates', json={'deep': True}).status_code == 202
    found = _reactions(fic_id)
    assert found['101'][0] == 'Like' and found['101'][1] > 5
    assert found['102'][0] is None and found['102'][1] > 5
    untouched = db.execute('SELECT COUNT(*) FROM fic_chapters WHERE fic_id=? AND updated_at != 7',
                           (fic_id,)).fetchone()[0]
    assert untouched == 0


def test_probe_reports_reactions_and_a_lapsed_session(monkeypatch):
    from types import SimpleNamespace
    from backend.fanfic import download, reaction_probe
    html = ('<html data-logged-in="false"><article class="message" data-content="post-9">'
            '<div class="bbWrapper">x</div></article></html>')
    monkeypatch.setattr(download, '_fetch', lambda url: SimpleNamespace(text=html))
    out = reaction_probe.probe('https://forums.example.com/threads/x.1/reader/')
    assert 'logged in: False' in out and 'post-9' in out and 'no react button' in out
    assert 'Fanfic site cookies' in out
