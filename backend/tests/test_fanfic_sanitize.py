from backend.fanfic.sanitize import (
    count_words,
    html_to_text,
    sanitize_chapter_html,
    strip_escaped_image_fallbacks,
)


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


def test_noscript_fallback_image_is_dropped_with_its_content():
    # XenForo emits a <noscript> copy of every inline image. Its children are
    # raw text per the HTML spec, so dropping the tag alone used to leave the
    # markup behind as visible escaped text.
    html = ('<div><img src="/api/fanfic/f1/images/a.jpg" alt="a.jpg">'
            '<noscript><img alt="a.jpg" class="bbImage" data-zoom-target="1"'
            ' src="/api/fanfic/f1/images/a.jpg" style="width: 441px"/></noscript></div>')
    clean = sanitize_chapter_html(html)
    assert '&lt;img' not in clean
    assert 'bbImage' not in clean
    assert clean.count('src="/api/fanfic/f1/images/a.jpg"') == 1


def test_strip_escaped_image_fallbacks_removes_duplicate_of_real_image():
    stored = ('<div><img alt="a.jpg" src="/api/fanfic/f1/images/a.jpg" title="">\n'
              '&lt;img alt="a.jpg" class="bbImage" data-zoom-target="1" height=""'
              ' src="/api/fanfic/f1/images/a.jpg" style="" title="" width=""/&gt;\n'
              '</div>')
    fixed = strip_escaped_image_fallbacks(stored)
    assert '&lt;img' not in fixed
    assert '<img alt="a.jpg" src="/api/fanfic/f1/images/a.jpg" title="">' in fixed


def test_strip_escaped_image_fallbacks_keeps_markup_quoted_in_prose():
    # No real <img> with that src, so this is the author writing about markup.
    stored = '<p>Use &lt;img src="cover.png"&gt; in the header.</p>'
    assert strip_escaped_image_fallbacks(stored) == stored

    stored2 = ('<p><img src="/api/fanfic/f1/images/a.jpg">'
               ' then &lt;img src="other.png"&gt;</p>')
    assert '&lt;img src="other.png"&gt;' in strip_escaped_image_fallbacks(stored2)


def test_html_to_text_and_word_count():
    text = html_to_text('<p>It began on a <b>Tuesday</b>.</p><p>Second line</p>')
    assert 'Tuesday' in text
    assert '<' not in text
    assert count_words('one two  three\nfour') == 4
