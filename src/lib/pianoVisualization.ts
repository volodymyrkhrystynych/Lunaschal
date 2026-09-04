import type { PianoHand, PracticeStep } from './piano';

const BLACK_PITCH_CLASSES = new Set([1, 3, 6, 8, 10]);
const FIRST_NOTE = 21;
const LAST_NOTE = 108;

export const PIANO_WHITE_KEY_COUNT = 52;

export interface PianoKeyGeometry {
  leftPercent: number;
  widthPercent: number;
  black: boolean;
}

export interface FallingNote {
  id: string;
  note: number;
  hand: 'right' | 'left';
  beatOffset: number;
  durationBeats: number;
  current: boolean;
}

export function isBlackPianoKey(note: number): boolean {
  return BLACK_PITCH_CLASSES.has(note % 12);
}

export function pianoKeyGeometry(note: number): PianoKeyGeometry | null {
  if (note < FIRST_NOTE || note > LAST_NOTE) return null;
  const notes = Array.from(
    { length: LAST_NOTE - FIRST_NOTE + 1 },
    (_, index) => index + FIRST_NOTE
  );
  const whiteNotes = notes.filter(value => !isBlackPianoKey(value));
  const whiteWidth = 100 / PIANO_WHITE_KEY_COUNT;
  if (!isBlackPianoKey(note)) {
    const index = whiteNotes.indexOf(note);
    return {
      leftPercent: index * whiteWidth,
      widthPercent: whiteWidth,
      black: false,
    };
  }

  let previousWhiteIndex = -1;
  for (let index = 0; index < whiteNotes.length; index += 1) {
    if (whiteNotes[index] >= note) break;
    previousWhiteIndex = index;
  }
  const widthPercent = whiteWidth * 0.62;
  return {
    leftPercent: (previousWhiteIndex + 1) * whiteWidth - widthPercent / 2,
    widthPercent,
    black: true,
  };
}

export function buildFallingNotes(
  steps: PracticeStep[],
  currentIndex: number,
  hand: PianoHand,
  visibleBeats = 8,
  elapsedBeats?: number
): FallingNote[] {
  const notes: FallingNote[] = [];
  const timelineBeat =
    elapsedBeats ??
    steps
      .slice(0, currentIndex)
      .reduce((total, step) => total + step.durationBeats, 0);
  let beatOffset = 0;
  for (let index = 0; index < steps.length; index += 1) {
    const step = steps[index];
    const relativeBeat = beatOffset - timelineBeat;
    if (relativeBeat > visibleBeats) break;
    if (index < currentIndex) {
      beatOffset += step.durationBeats;
      continue;
    }
    const hands =
      hand === 'both'
        ? (['right', 'left'] as const)
        : ([hand] as Array<'right' | 'left'>);
    hands.forEach(selectedHand => {
      step[selectedHand].forEach((note, noteIndex) => {
        notes.push({
          id: `${index}:${selectedHand}:${note}:${noteIndex}`,
          note,
          hand: selectedHand,
          beatOffset: relativeBeat,
          durationBeats: step.durationBeats,
          current: index === currentIndex,
        });
      });
    });
    beatOffset += step.durationBeats;
  }
  return notes;
}

export function midiNoteName(note: number): string {
  const names = [
    'C',
    'C♯',
    'D',
    'D♯',
    'E',
    'F',
    'F♯',
    'G',
    'G♯',
    'A',
    'A♯',
    'B',
  ];
  return `${names[note % 12]}${Math.floor(note / 12) - 1}`;
}
