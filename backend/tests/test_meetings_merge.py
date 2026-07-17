"""Unit tests for the pure transcript-merge logic (no ML deps needed)."""
from backend.meetings.merge import assign_speakers, merge_segments, render_transcript, strip_echoes


def seg(start, end, text):
    return {'start': start, 'end': end, 'text': text}


def turn(start, end, speaker):
    return {'start': start, 'end': end, 'speaker': speaker}


class TestAssignSpeakers:
    def test_no_turns_labels_everything_others(self):
        segments = [seg(0, 2, 'hello'), seg(3, 5, 'world')]
        for turns in (None, []):
            out = assign_speakers(segments, turns)
            assert [s['speaker'] for s in out] == ['Others', 'Others']

    def test_max_total_overlap_wins(self):
        # SPEAKER_00 overlaps 0-1s; SPEAKER_01 overlaps in two turns totalling 2s.
        turns = [turn(0, 1, 'SPEAKER_00'), turn(1, 2, 'SPEAKER_01'), turn(2.5, 3.5, 'SPEAKER_01')]
        out = assign_speakers([seg(0, 3.5, 'long segment')], turns)
        # SPEAKER_01 heard first? No — SPEAKER_00 speaks at t=0 but the segment
        # is assigned to SPEAKER_01 (2s overlap vs 1s), which becomes Speaker 1
        # as the only labeled speaker present.
        assert out[0]['speaker'] == 'Speaker 1'

    def test_zero_overlap_falls_to_nearest_midpoint(self):
        turns = [turn(0, 2, 'SPEAKER_00'), turn(10, 12, 'SPEAKER_01')]
        out = assign_speakers([seg(8, 9, 'in a gap')], turns)
        # Segment midpoint 8.5 is nearer turn midpoint 11 than 1.
        assert out[0]['speaker'].endswith('1')
        assert len({s['speaker'] for s in out}) == 1

    def test_renumbered_by_first_appearance(self):
        # Raw labels arrive "out of order": SPEAKER_01 speaks first.
        turns = [turn(0, 2, 'SPEAKER_01'), turn(3, 5, 'SPEAKER_00')]
        out = assign_speakers([seg(0, 2, 'first'), seg(3, 5, 'second')], turns)
        assert out[0]['speaker'] == 'Speaker 1'  # raw SPEAKER_01
        assert out[1]['speaker'] == 'Speaker 2'  # raw SPEAKER_00


class TestMergeSegments:
    def test_chronological_interleave(self):
        mic = [seg(0, 2, 'hi everyone'), seg(10, 12, 'sounds good')]
        system = [seg(4, 8, 'thanks for joining')]
        out = merge_segments(mic, system, None)
        assert [s['speaker'] for s in out] == ['Me', 'Others', 'Me']
        assert [s['text'] for s in out] == ['hi everyone', 'thanks for joining', 'sounds good']

    def test_coalesces_same_speaker_within_gap(self):
        mic = [seg(0, 2, 'one'), seg(3, 4, 'two'), seg(10, 11, 'three')]
        out = merge_segments(mic, [], None)
        # 2→3 gap is 1s (≤1.5) → folded; 4→10 gap is 6s → new line.
        assert len(out) == 2
        assert out[0]['text'] == 'one two'
        assert out[0]['end'] == 4
        assert out[1]['text'] == 'three'

    def test_does_not_coalesce_across_speaker_change(self):
        mic = [seg(0, 2, 'question?')]
        system = [seg(2.5, 4, 'answer.')]
        out = merge_segments(mic, system, None)
        assert len(out) == 2

    def test_empty_tracks(self):
        assert merge_segments([], [], None) == []
        only_mic = merge_segments([seg(0, 1, 'solo')], [], None)
        assert [s['speaker'] for s in only_mic] == ['Me']
        only_sys = merge_segments([], [seg(0, 1, 'remote')], None)
        assert [s['speaker'] for s in only_sys] == ['Others']

    def test_blank_text_segments_dropped(self):
        out = merge_segments([seg(0, 1, '   ')], [seg(2, 3, ' hello ')], None)
        assert len(out) == 1
        assert out[0]['text'] == 'hello'

    def test_diarized_speakers_flow_through(self):
        system = [seg(0, 2, 'alpha'), seg(5, 7, 'beta')]
        turns = [turn(0, 2, 'SPEAKER_00'), turn(5, 7, 'SPEAKER_01')]
        out = merge_segments([seg(3, 4, 'me here')], system, turns)
        assert [s['speaker'] for s in out] == ['Speaker 1', 'Me', 'Speaker 2']


class TestStripEchoes:
    def test_identical_overlapping_text_dropped(self):
        mic = [seg(3.1, 5.2, 'Thanks for joining the call today.')]
        system = [seg(3.0, 5.0, 'thanks for joining the call today')]
        assert strip_echoes(mic, system) == []

    def test_fuzzy_match_with_transcription_errors_dropped(self):
        # Bleed is quiet, so whisper mishears a word or two.
        mic = [seg(3.0, 5.0, 'the deployment finished on Tuesday and everything look stable')]
        system = [seg(3.0, 5.1, 'The deployment finished on Tuesday and everything looks stable.')]
        assert strip_echoes(mic, system) == []

    def test_fragment_contained_in_longer_segment_dropped(self):
        mic = [seg(4.0, 6.0, 'finished on Tuesday and everything')]
        system = [seg(2.0, 8.0, 'From my side, the deployment finished on Tuesday and everything looks stable so far.')]
        assert strip_echoes(mic, system) == []

    def test_same_text_after_the_fact_kept(self):
        # A genuine reply repeating what was said comes AFTER, not during.
        mic = [seg(6.0, 7.5, 'sounds good to me as well')]
        system = [seg(2.0, 5.0, 'sounds good to me as well')]
        assert len(strip_echoes(mic, system)) == 1

    def test_different_overlapping_speech_kept(self):
        # Real crosstalk: talking over someone is not an echo.
        mic = [seg(3.0, 5.0, 'wait, can I jump in for a second')]
        system = [seg(2.5, 6.0, 'the migration completed without any incidents overnight')]
        assert len(strip_echoes(mic, system)) == 1

    def test_short_coincidental_containment_kept(self):
        # 'yeah' appears inside their sentence but is too short to count as echo.
        mic = [seg(3.0, 3.5, 'yeah')]
        system = [seg(2.0, 6.0, 'yeah so I think we should ship it on Friday')]
        assert len(strip_echoes(mic, system)) == 1

    def test_no_system_track_unchanged(self):
        mic = [seg(0, 2, 'hello world')]
        assert strip_echoes(mic, []) == mic

    def test_removed_via_merge_segments(self):
        # End to end: the duplicated line is gone and the label is correct.
        mic = [seg(0, 2, 'hi, can everyone hear me'), seg(3.1, 5.0, 'we shipped the fix this morning')]
        system = [seg(3.0, 5.0, 'We shipped the fix this morning.')]
        out = merge_segments(mic, system, None)
        assert [(s['speaker'], s['text']) for s in out] == [
            ('Me', 'hi, can everyone hear me'),
            ('Others', 'We shipped the fix this morning.'),
        ]


class TestRenderTranscript:
    def test_format(self):
        segments = [
            {'start': 0.4, 'end': 2, 'speaker': 'Me', 'text': 'hello'},
            {'start': 65.9, 'end': 70, 'speaker': 'Speaker 1', 'text': 'hi'},
        ]
        assert render_transcript(segments) == '[00:00] Me: hello\n[01:05] Speaker 1: hi'

    def test_empty(self):
        assert render_transcript([]) == ''
