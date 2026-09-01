from backend.piano.midi import MidiStreamParser


def test_parses_note_on_note_off_and_sustain():
    parser = MidiStreamParser()

    events = parser.feed(bytes([0x90, 60, 100, 0x80, 60, 32, 0xB0, 64, 127]))

    assert [event.as_dict() for event in events] == [
        {'kind': 'noteOn', 'note': 60, 'velocity': 100, 'value': None},
        {'kind': 'noteOff', 'note': 60, 'velocity': 32, 'value': None},
        {'kind': 'sustain', 'note': None, 'velocity': None, 'value': 127},
    ]


def test_supports_running_status_and_zero_velocity_note_off():
    parser = MidiStreamParser()

    events = parser.feed(bytes([0x90, 60, 90, 64, 0, 67, 80]))

    assert [(event.kind, event.note) for event in events] == [
        ('noteOn', 60),
        ('noteOff', 64),
        ('noteOn', 67),
    ]


def test_keeps_partial_message_between_reads_and_ignores_clock():
    parser = MidiStreamParser()

    assert parser.feed(bytes([0x90, 72])) == []
    events = parser.feed(bytes([0xF8, 110]))

    assert len(events) == 1
    assert events[0].note == 72
