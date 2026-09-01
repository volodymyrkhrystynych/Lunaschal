from __future__ import annotations

from dataclasses import dataclass
from glob import glob
from pathlib import Path


@dataclass(frozen=True)
class MidiEvent:
    kind: str
    note: int | None = None
    velocity: int | None = None
    value: int | None = None

    def as_dict(self) -> dict[str, int | str | None]:
        return {
            'kind': self.kind,
            'note': self.note,
            'velocity': self.velocity,
            'value': self.value,
        }


def list_raw_midi_devices(pattern: str = '/dev/snd/midiC*D*') -> list[dict[str, str]]:
    """Return Linux raw-MIDI devices in stable order.

    Raw ALSA devices are byte streams, so using them here avoids a native Python
    dependency in the always-running app. The desktop bridge opens the selected
    path only after the user asks it to connect.
    """
    return [
        {'id': path, 'name': Path(path).name}
        for path in sorted(glob(pattern))
    ]


class MidiStreamParser:
    """Incrementally parse the channel messages needed by a piano keyboard."""

    def __init__(self) -> None:
        self._status: int | None = None
        self._data: list[int] = []

    def feed(self, chunk: bytes) -> list[MidiEvent]:
        events: list[MidiEvent] = []
        for byte in chunk:
            if byte >= 0xF8:  # real-time clock bytes may occur between data bytes
                continue
            if byte & 0x80:
                if byte >= 0xF0:  # system messages are irrelevant to key practice
                    self._status = None
                    self._data.clear()
                    continue
                self._status = byte
                self._data.clear()
                continue
            if self._status is None:
                continue
            self._data.append(byte)
            needed = 1 if (self._status & 0xE0) == 0xC0 else 2
            if len(self._data) < needed:
                continue
            event = self._event(self._status, self._data[:needed])
            self._data = self._data[needed:]
            if event is not None:
                events.append(event)
        return events

    @staticmethod
    def _event(status: int, data: list[int]) -> MidiEvent | None:
        message = status & 0xF0
        if message == 0x90:
            note, velocity = data
            return MidiEvent(
                kind='noteOff' if velocity == 0 else 'noteOn',
                note=note,
                velocity=velocity,
            )
        if message == 0x80:
            note, velocity = data
            return MidiEvent(kind='noteOff', note=note, velocity=velocity)
        if message == 0xB0 and data[0] == 64:
            return MidiEvent(kind='sustain', value=data[1])
        return None
