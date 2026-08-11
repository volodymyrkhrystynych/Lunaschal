"""Pure tests for backend/email/sanitize.py — no DB, no network.

The security-relevant claims are the point here: script never survives, and
no <img> is ever left pointing at a remote host, because both are things a
sender chooses and we render.
"""
from backend.email.media import url_hash
from backend.email.sanitize import sanitize_email_html


def test_script_and_event_handlers_are_removed():
    html, _ = sanitize_email_html(
        '<div>Hi<script>alert(1)</script>'
        '<a href="https://x.example" onclick="steal()">click</a></div>'
    )
    assert 'script' not in html.lower()
    assert 'alert' not in html
    assert 'onclick' not in html.lower()
    assert 'click' in html


def test_remote_image_is_rewritten_to_a_local_path_and_reported():
    src = 'https://cdn.example/logo.png?utm=1'
    html, images = sanitize_email_html(f'<p><img src="{src}" alt="Logo"></p>')

    digest = url_hash(src)
    assert f'/api/email/images/{digest}' in html
    assert 'cdn.example' not in html
    assert images == [(digest, src)]


def test_no_img_keeps_a_src_attribute():
    """The whole privacy claim rests on this: after sanitizing, nothing in
    the markup can cause the browser to reach a sender's server."""
    html, _ = sanitize_email_html(
        '<img src="https://track.example/pixel.gif" width="1" height="1">'
    )
    assert ' src=' not in html
    assert 'data-src=' in html


def test_srcset_is_dropped():
    """srcset would reintroduce the remote fetch that rewriting src prevents,
    and an allowed-attribute list can't stop what it doesn't enumerate."""
    html, _ = sanitize_email_html(
        '<img src="https://cdn.example/a.png" srcset="https://cdn.example/a2x.png 2x">'
    )
    assert 'srcset' not in html.lower()
    assert 'a2x.png' not in html


def test_javascript_and_data_image_urls_are_dropped_not_deferred():
    """A data-src is an opaque string to nh3, and the frontend promotes it to
    a real src — so a non-http(s) scheme has to be filtered before it gets
    there, not left for the sanitizer to catch."""
    html, images = sanitize_email_html(
        '<img src="javascript:alert(1)"><img src="data:image/png;base64,AAAA">'
    )
    assert 'javascript:' not in html
    assert 'data:image' not in html
    assert images == []


def test_style_blocks_are_dropped_with_their_content():
    """An email's <head> stylesheet is often larger than its prose; letting
    the tag through as text would dump CSS into the reader."""
    html, _ = sanitize_email_html(
        '<style>.x{color:red}</style><p>Body</p>'
    )
    assert 'color:red' not in html
    assert 'Body' in html


def test_style_attributes_are_stripped():
    """nh3 does not parse CSS, so a permitted style attribute would pass
    url(...) through untouched — the same tracking fetch in another hat."""
    html, _ = sanitize_email_html(
        '<p style="background:url(https://track.example/p.gif)">Hi</p>'
    )
    assert 'track.example' not in html
    assert 'style=' not in html


def test_links_survive_with_rel_and_are_not_localized():
    html, _ = sanitize_email_html('<a href="https://jobs.example/apply">Apply</a>')
    assert 'https://jobs.example/apply' in html
    assert 'noopener' in html


def test_duplicate_image_urls_are_reported_once():
    """A signature logo repeated down a quoted reply chain is one fetch."""
    src = 'https://cdn.example/sig.png'
    _, images = sanitize_email_html(f'<img src="{src}"><img src="{src}"><img src="{src}">')
    assert images == [(url_hash(src), src)]


def test_empty_html_is_empty():
    assert sanitize_email_html('') == ('', [])
    assert sanitize_email_html('   ') == ('', [])


def test_tables_survive_since_newsletters_are_built_from_them():
    html, _ = sanitize_email_html(
        '<table><tr><td colspan="2">Cell</td></tr></table>'
    )
    assert '<table>' in html
    assert 'colspan' in html
    assert 'Cell' in html
