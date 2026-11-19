"""The posting body rebuilt as Markdown from each board's raw payload."""
import json

from backend.jobs.posting_text import html_to_markdown, posting_markdown, source_html


def test_headings_lists_and_inline_emphasis_survive():
    html = ('<h2>About the role</h2><p>We use <strong>Swift</strong> daily and '
            '<a href="https://x.test/team">meet often</a>.</p>'
            '<ul><li>Own the sync layer</li><li>Ship <em>weekly</em></li></ul>'
            '<ol><li>Apply</li><li>Chat</li></ol>')
    assert html_to_markdown(html) == (
        '### About the role\n\n'
        'We use **Swift** daily and [meet often](https://x.test/team).\n\n'
        '- Own the sync layer\n- Ship *weekly*\n\n'
        '1. Apply\n2. Chat'
    )


def test_a_bold_paragraph_on_its_own_is_a_heading():
    assert html_to_markdown('<p><strong>What you bring:</strong></p><p>Care.</p>') == (
        '### What you bring\n\nCare.'
    )


def test_a_bold_heading_is_just_a_heading():
    assert html_to_markdown('<h3><strong>Who we are</strong></h3>') == '### Who we are'


def test_nested_lists_indent_and_breaks_split_lines():
    html = '<ul><li>Backend<ul><li>Python</li></ul></li></ul><p>One<br>Two</p>'
    assert html_to_markdown(html) == '- Backend\n  - Python\n\nOne\n\nTwo'


def test_markdown_characters_in_the_text_stay_text_and_scripts_vanish():
    html = '<p>Use *args and [brackets]</p><script>alert(1)</script><p><b> </b></p>'
    assert html_to_markdown(html) == r'Use \*args and \[brackets\]'


def test_a_link_the_phone_cannot_follow_keeps_its_words():
    assert html_to_markdown('<p>See <a href="javascript:x()">here</a>.</p>') == 'See here.'


def test_each_board_payload_shape():
    greenhouse = {'content': '&lt;p&gt;Hello &lt;b&gt;there&lt;/b&gt;&lt;/p&gt;'}
    assert source_html(greenhouse) == '<p>Hello <b>there</b></p>'
    assert source_html({'descriptionHtml': '<p>Ashby</p>', 'descriptionPlain': 'Ashby'}) == '<p>Ashby</p>'
    lever = {'descriptionPlain': 'x', 'description': '<p>Intro</p>',
             'lists': [{'text': 'Requirements', 'content': '<li>Go</li>'}]}
    assert html_to_markdown(source_html(lever)) == 'Intro\n\n### Requirements\n\n- Go'
    workday = {'listing': {}, 'detail': {'jobPostingInfo': {'jobDescription': '<p>WD</p>'}}}
    assert source_html(json.dumps(workday)) == '<p>WD</p>'
    assert source_html({'description': 'adzuna snippet'}) is None
    assert source_html(None) is None
    assert source_html('not json') is None


def test_without_html_the_stored_text_becomes_paragraphs():
    assert posting_markdown(None, 'Line one\n\nLine *two*\n') == 'Line one\n\nLine \\*two\\*'
    assert posting_markdown({'content': '&lt;p&gt;From HTML&lt;/p&gt;'}, 'stored') == 'From HTML'


def test_the_job_route_sends_it(client):
    job_id = client.post('/api/jobs', json={
        'title': 'Engineer', 'company': 'Acme', 'description': 'Plain\nlines',
    }).get_json()['id']
    body = client.get(f'/api/jobs/{job_id}').get_json()
    assert body['descriptionMarkdown'] == 'Plain\n\nlines'
