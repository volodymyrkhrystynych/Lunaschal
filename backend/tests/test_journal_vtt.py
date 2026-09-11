"""backend/journal/vtt.py — WebVTT to plain text.

Pure, so no fixtures: every case here is a string in and a string out.
"""
from backend.journal.vtt import vtt_to_text

AUTO_CAPTIONS = """WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:03.000 align:start position:0%
the first thing they said

00:00:03.000 --> 00:00:05.000 align:start position:0%
the first thing they said
and then the second thing

00:00:05.000 --> 00:00:07.000 align:start position:0%
and then the second thing
and then the third
"""


def test_rolling_auto_caption_window_collapses_to_one_copy():
    """YouTube's auto-captions repeat each line as it scrolls out of the window,
    so a naive join roughly doubles the transcript — and doubles what the
    summarizing model has to read."""
    assert vtt_to_text(AUTO_CAPTIONS) == (
        'the first thing they said\n'
        'and then the second thing\n'
        'and then the third'
    )


def test_inline_karaoke_markup_and_entities_are_stripped():
    raw = (
        'WEBVTT\n\n'
        '00:00:01.000 --> 00:00:02.000\n'
        'tea <00:00:01.400><c>&amp;</c> biscuits\n'
    )
    assert vtt_to_text(raw) == 'tea & biscuits'


def test_cue_identifiers_are_dropped_but_a_numeric_caption_is_not():
    """A bare integer is a cue id only directly above the timing line. Below it
    the same characters are something somebody said."""
    raw = (
        'WEBVTT\n\n'
        '1\n00:00:01.000 --> 00:00:02.000\nthe year was\n\n'
        '2\n00:00:02.000 --> 00:00:03.000\n2024\n'
    )
    assert vtt_to_text(raw) == 'the year was\n2024'


def test_note_blocks_go_but_a_caption_starting_with_note_stays():
    """`NOTE` introduces a comment only as the first line of a block. Matching it
    anywhere would eat a real caption and the rest of its cue."""
    raw = (
        'WEBVTT\n\n'
        'NOTE this is a comment\nspanning two lines\n\n'
        '00:00:01.000 --> 00:00:02.000\nNOTE that this matters\n'
    )
    assert vtt_to_text(raw) == 'NOTE that this matters'


def test_style_and_region_blocks_are_dropped():
    raw = (
        'WEBVTT\n\n'
        'STYLE\n::cue { color: peachpuff }\n\n'
        'REGION\nid:speaker width:40%\n\n'
        '00:00:01.000 --> 00:00:02.000\nhello\n'
    )
    assert vtt_to_text(raw) == 'hello'


def test_short_timestamp_form_is_recognised():
    """WebVTT allows mm:ss.mmm as well as hh:mm:ss.mmm."""
    raw = 'WEBVTT\n\n00:01.000 --> 00:02.000\nhello\n'
    assert vtt_to_text(raw) == 'hello'


def test_a_byte_order_mark_does_not_hide_the_header():
    raw = '﻿WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello\n'
    assert vtt_to_text(raw) == 'hello'


def test_crlf_line_endings_still_split_into_blocks():
    raw = 'WEBVTT\r\n\r\n00:00:01.000 --> 00:00:02.000\r\nhello\r\n'
    assert vtt_to_text(raw) == 'hello'


def test_a_repeated_line_that_is_not_adjacent_survives():
    """Dedupe is about the scrolling window, not about unique lines: a phrase
    genuinely said twice, with something in between, is said twice."""
    raw = (
        'WEBVTT\n\n'
        '00:00:01.000 --> 00:00:02.000\nyes\n\n'
        '00:00:02.000 --> 00:00:03.000\nno\n\n'
        '00:00:03.000 --> 00:00:04.000\nyes\n'
    )
    assert vtt_to_text(raw) == 'yes\nno\nyes'


def test_empty_and_contentless_files_yield_nothing():
    """'' is the signal the caller falls back to transcribing audio on, so these
    must not come back as whitespace that reads as a transcript."""
    assert vtt_to_text('') == ''
    assert vtt_to_text('WEBVTT\n') == ''
    assert vtt_to_text('WEBVTT\n\nNOTE nothing here\n') == ''
    assert vtt_to_text('not a vtt file at all') == ''
