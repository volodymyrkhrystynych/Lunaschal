"""Pure transcript-merging logic for meeting recordings.

Inputs are plain dicts so this module stays free of whisper/pyannote imports
and can be unit-tested without any ML dependencies:

- whisper segments: {'start': float, 'end': float, 'text': str}
- diarization turns: {'start': float, 'end': float, 'speaker': str}
  (raw pyannote labels like 'SPEAKER_00'), or None when diarization is
  unavailable — then every system-track segment is labeled 'Others'.
"""

import re
from difflib import SequenceMatcher

# Consecutive same-speaker segments closer than this (seconds) are folded
# into one transcript line.
_COALESCE_GAP = 1.5

# Echo removal: a mic segment is considered speaker bleed (the microphone
# picking up remote participants played through speakers) when it temporally
# overlaps a system segment AND its text is near-identical to it.
_ECHO_SIMILARITY = 0.8   # SequenceMatcher ratio threshold
_ECHO_OVERLAP_TOL = 0.5  # seconds of slack for whisper's imprecise timestamps
_ECHO_MIN_CONTAINED = 10  # min normalized chars before containment counts as echo

_NON_WORD = re.compile(r'[^a-z0-9 ]+')


def _norm_text(text: str) -> str:
    return ' '.join(_NON_WORD.sub(' ', text.lower()).split())


def strip_echoes(mic_segments: list[dict], system_segments: list[dict]) -> list[dict]:
    """Drop mic segments that are echoes of the system track.

    Without headphones, the mic hears the remote participants through the
    speakers, so their sentences get transcribed on the mic track too and
    would be mislabeled 'Me'. Bleed is simultaneous with the original and
    transcribes to near-identical text, so: drop a mic segment when it
    overlaps a system segment in time and the text either fuzzy-matches it
    or is contained in it. Genuine replies (repeating what someone said)
    survive because they come *after* the original, not during it.
    """
    if not system_segments:
        return mic_segments
    kept = []
    for m in mic_segments:
        m_text = _norm_text(m['text'])
        is_echo = False
        if m_text:
            for s in system_segments:
                overlap = (min(m['end'], s['end'] + _ECHO_OVERLAP_TOL)
                           - max(m['start'], s['start'] - _ECHO_OVERLAP_TOL))
                if overlap <= 0:
                    continue
                s_text = _norm_text(s['text'])
                if not s_text:
                    continue
                similar = SequenceMatcher(None, m_text, s_text).ratio() >= _ECHO_SIMILARITY
                contained = len(m_text) >= _ECHO_MIN_CONTAINED and m_text in s_text
                if similar or contained:
                    is_echo = True
                    break
        if not is_echo:
            kept.append(m)
    return kept


def assign_speakers(system_segments: list[dict], turns: list[dict] | None) -> list[dict]:
    """Label each system-track segment with the diarized speaker that overlaps
    it most; falls back to 'Others' for all segments when turns are missing."""
    if not turns:
        return [{**seg, 'speaker': 'Others'} for seg in system_segments]

    labeled = []
    for seg in system_segments:
        overlaps: dict[str, float] = {}
        for turn in turns:
            ov = max(0.0, min(seg['end'], turn['end']) - max(seg['start'], turn['start']))
            if ov > 0:
                overlaps[turn['speaker']] = overlaps.get(turn['speaker'], 0.0) + ov
        if overlaps:
            # Max total overlap; ties break deterministically by label sort order.
            speaker = min(overlaps, key=lambda s: (-overlaps[s], s))
        else:
            # Segment falls in a diarization gap: nearest turn midpoint wins.
            seg_mid = (seg['start'] + seg['end']) / 2
            nearest = min(turns, key=lambda t: (abs((t['start'] + t['end']) / 2 - seg_mid), t['speaker']))
            speaker = nearest['speaker']
        labeled.append({**seg, 'speaker': speaker})

    # Renumber raw labels to 'Speaker 1..N' by first appearance in time order.
    names: dict[str, str] = {}
    for seg in sorted(labeled, key=lambda s: s['start']):
        if seg['speaker'] not in names:
            names[seg['speaker']] = f'Speaker {len(names) + 1}'
    return [{**seg, 'speaker': names[seg['speaker']]} for seg in labeled]


def merge_segments(mic_segments: list[dict], system_segments: list[dict],
                   turns: list[dict] | None) -> list[dict]:
    """Merge both tracks into one chronological, speaker-labeled segment list."""
    mic_segments = strip_echoes(mic_segments, system_segments)
    mine = [{**seg, 'speaker': 'Me'} for seg in mic_segments]
    others = assign_speakers(system_segments, turns)
    # Stable sort with 'Me' first on exact start-time ties.
    merged = sorted(mine + others, key=lambda s: (s['start'], s['speaker'] != 'Me'))

    coalesced: list[dict] = []
    for seg in merged:
        text = seg['text'].strip()
        if not text:
            continue
        prev = coalesced[-1] if coalesced else None
        if prev and prev['speaker'] == seg['speaker'] and seg['start'] - prev['end'] <= _COALESCE_GAP:
            prev['text'] += ' ' + text
            prev['end'] = max(prev['end'], seg['end'])
        else:
            coalesced.append({'start': seg['start'], 'end': seg['end'],
                              'speaker': seg['speaker'], 'text': text})
    return coalesced


def render_transcript(segments: list[dict]) -> str:
    lines = []
    for seg in segments:
        total = int(seg['start'])
        lines.append(f"[{total // 60:02d}:{total % 60:02d}] {seg['speaker']}: {seg['text']}")
    return '\n'.join(lines)
