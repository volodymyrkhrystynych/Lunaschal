from backend.fanfic.sanitize import count_words, html_to_text, sanitize_chapter_html


def test_strips_script_and_event_handlers():
    dirty = '<p>hi<script>alert(1)</script></p><span onclick="steal()">ok</span>'
    clean = sanitize_chapter_html(dirty)
    assert '<script' not in clean
    assert 'alert' not in clean
    assert 'onclick' not in clean
    assert 'ok' in clean


def test_strips_javascript_href():
    clean = sanitize_chapter_html('<a href="javascript:evil()">x</a>')
    assert 'javascript' not in clean


def test_relative_api_image_src_survives():
    # Pins the nh3/ammonia default of passing relative URLs through — the
    # whole local-image pipeline depends on it.
    clean = sanitize_chapter_html('<img src="/api/fanfic/f1/images/abc.png" alt="art">')
    assert 'src="/api/fanfic/f1/images/abc.png"' in clean
    assert 'alt="art"' in clean


def test_keeps_formatting_and_tables():
    html = ('<blockquote>quote</blockquote><table><tbody><tr><td>HP</td></tr></tbody></table>'
            '<b>bold</b><i>it</i>')
    clean = sanitize_chapter_html(html)
    for frag in ('<blockquote>', '<table>', '<td>', '<b>', '<i>'):
        assert frag in clean


def test_absolute_http_image_allowed():
    clean = sanitize_chapter_html('<img src="https://example.com/a.png">')
    assert 'https://example.com/a.png' in clean


def test_html_to_text_and_word_count():
    text = html_to_text('<p>It began on a <b>Tuesday</b>.</p><p>Second line</p>')
    assert 'Tuesday' in text
    assert '<' not in text
    assert count_words('one two  three\nfour') == 4
