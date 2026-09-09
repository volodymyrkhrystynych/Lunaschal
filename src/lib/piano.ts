export type PianoHand = 'right' | 'left' | 'both';

export interface PianoPiece {
  id: string;
  title: string;
  composer: string | null;
  sourceFilename: string;
  createdAt: string;
  updatedAt: string;
}

export interface PianoPreferences {
  sessionMinutes: number;
  skillLevel: 'beginner' | 'intermediate' | 'advanced';
  jazzPercent: number;
  updatedAt: string;
}

export interface PianoAttempt {
  id: string;
  completedAt: string;
  tempo: number | null;
  correctNotes: number | null;
  wrongNotes: number | null;
  onsetAccuracy: number | null;
  durationAccuracy: number | null;
  tempoStability: number | null;
  velocityEvenness: number | null;
  achievedTempo: number | null;
  selfRating: number | null;
  notes: string | null;
}

export interface PianoDailyExercise {
  id: string;
  exerciseKey: string;
  title: string;
  category: string;
  style: 'shared' | 'classical' | 'jazz';
  description: string;
  instructions: string;
  keyName: string | null;
  targetTempo: number | null;
  minutes: number;
  gradeable: boolean;
  pianoPieceId: string | null;
  measureStart: number | null;
  measureEnd: number | null;
  pieceTitle?: string | null;
  completedAt: string | null;
  latestAttempt: PianoAttempt | null;
}

export interface PianoToday {
  dayKey: string;
  preferences: PianoPreferences;
  exercises: PianoDailyExercise[];
}

export interface PianoHistoryDay {
  dayKey: string;
  exerciseCount: number;
  completedCount: number;
  minutesPlanned: number;
  onsetAccuracy: number | null;
  tempoStability: number | null;
  velocityEvenness: number | null;
}

export interface PianoArchiveItem {
  id: string;
  collection: 'piano';
  title: string;
  creator: string | null;
  mediaType:
    | 'score'
    | 'midi'
    | 'document'
    | 'archive'
    | 'audio'
    | 'video'
    | 'image'
    | 'file';
  sourceFilename: string;
  relativePath: string;
  sourceUrl: string | null;
  contentType: string | null;
  sizeBytes: number;
  sha256: string | null;
  practiceCompatible: number;
  favorite: number;
  pianoPieceId: string | null;
  available: boolean;
  fileUrl: string;
  createdAt: string;
  updatedAt: string;
}

export interface PianoArchivePage {
  items: PianoArchiveItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface PianoArchiveStatus {
  configured: boolean;
  available: boolean;
  writable: boolean;
  root: string | null;
  destination: string | null;
  reason: string | null;
  itemCount: number;
  favoriteCount: number;
  sizeBytes: number;
  freeBytes: number | null;
  totalBytes: number | null;
}

export interface PianoArchiveScanResult {
  indexed: number;
  updated: number;
  skipped: number;
}

export interface PracticeStep {
  measure: number;
  beat: number;
  right: number[];
  left: number[];
  durationBeats: number;
  onsetBeats?: number;
  noteDurations?: { right: number[]; left: number[] };
}

const SEMITONES: Record<string, number> = {
  C: 0,
  D: 2,
  E: 4,
  F: 5,
  G: 7,
  A: 9,
  B: 11,
};

function children(parent: Element, name: string): Element[] {
  return Array.from(parent.children).filter(node => node.localName === name);
}

function child(parent: Element, name: string): Element | undefined {
  return children(parent, name)[0];
}

function numberText(parent: Element, name: string, fallback = 0): number {
  const text = child(parent, name)?.textContent;
  if (text == null || !text.trim()) return fallback;
  const value = Number(text);
  return Number.isFinite(value) ? value : fallback;
}

function midiPitch(note: Element): number | null {
  const pitch = child(note, 'pitch');
  if (!pitch) return null;
  const step = child(pitch, 'step')?.textContent?.trim() ?? '';
  const octave = numberText(pitch, 'octave', -1);
  if (!(step in SEMITONES) || octave < 0) return null;
  const alter = numberText(pitch, 'alter');
  return (octave + 1) * 12 + SEMITONES[step] + alter;
}

export function parsePracticeSteps(xml: string): PracticeStep[] {
  const document = new DOMParser().parseFromString(xml, 'application/xml');
  if (document.querySelector('parsererror'))
    throw new Error('Invalid MusicXML score.');
  const part = Array.from(document.getElementsByTagName('*')).find(
    node => node.localName === 'part'
  );
  if (!part) return [];
  const steps = new Map<string, PracticeStep>();
  let divisions = 1;
  let measureStart = 0;
  const ties = new Map<
    string,
    { step: PracticeStep; hand: 'right' | 'left'; index: number; end: number }
  >();

  children(part, 'measure').forEach((measure, measureIndex) => {
    const number = Number(measure.getAttribute('number')) || measureIndex + 1;
    let cursor = 0;
    let previousOnset = 0;
    let measureLength = 0;
    for (const event of Array.from(measure.children)) {
      if (event.localName === 'attributes') {
        divisions = numberText(event, 'divisions', divisions) || divisions;
      } else if (event.localName === 'backup') {
        cursor -= numberText(event, 'duration') / divisions;
      } else if (event.localName === 'forward') {
        cursor += numberText(event, 'duration') / divisions;
        measureLength = Math.max(measureLength, cursor);
      } else if (event.localName === 'note') {
        const duration = numberText(event, 'duration') / divisions;
        const isChord = Boolean(child(event, 'chord'));
        const onset = isChord ? previousOnset : cursor;
        previousOnset = onset;
        const pitch = midiPitch(event);
        if (pitch !== null) {
          const staff = numberText(event, 'staff', pitch < 60 ? 2 : 1);
          const hand = staff === 2 ? 'left' : 'right';
          const voice = child(event, 'voice')?.textContent?.trim() ?? '1';
          const tieKey = `${staff}:${voice}:${pitch}`;
          const tieNodes = [
            ...children(event, 'tie'),
            ...children(event, 'notations').flatMap(node =>
              children(node, 'tied')
            ),
          ];
          const stops = tieNodes.some(
            node => node.getAttribute('type') === 'stop'
          );
          const starts = tieNodes.some(
            node => node.getAttribute('type') === 'start'
          );
          const previous = ties.get(tieKey);
          const absoluteOnset = measureStart + onset;
          if (
            stops &&
            previous &&
            Math.abs(previous.end - absoluteOnset) < 1e-7
          ) {
            previous.step.noteDurations![previous.hand][previous.index] +=
              duration;
            previous.end = absoluteOnset + duration;
            if (!starts) ties.delete(tieKey);
          } else {
            const key = `${measureIndex}:${onset}`;
            const step = steps.get(key) ?? {
              measure: number,
              beat: onset + 1,
              onsetBeats: absoluteOnset,
              right: [],
              left: [],
              durationBeats: duration,
              noteDurations: { right: [], left: [] },
            };
            step.durationBeats = Math.max(step.durationBeats, duration);
            const index = step[hand].length;
            step[hand].push(pitch);
            step.noteDurations![hand].push(duration);
            ties.delete(tieKey);
            if (starts)
              ties.set(tieKey, {
                step,
                hand,
                index,
                end: absoluteOnset + duration,
              });
            steps.set(key, step);
          }
        }
        if (!isChord) cursor += duration;
        measureLength = Math.max(measureLength, cursor, onset + duration);
      }
    }
    measureStart += measureLength;
  });
  const ordered = Array.from(steps.values()).sort(
    (a, b) => a.onsetBeats! - b.onsetBeats!
  );
  ordered.forEach((step, index) => {
    step.durationBeats =
      (ordered[index + 1]?.onsetBeats ?? measureStart) - step.onsetBeats!;
  });
  return ordered;
}

export function notesForHand(step: PracticeStep, hand: PianoHand): number[] {
  if (hand === 'right') return step.right;
  if (hand === 'left') return step.left;
  return [...step.right, ...step.left];
}

export function stepIsComplete(
  step: PracticeStep,
  hand: PianoHand,
  heldNotes: ReadonlySet<number>
): boolean {
  const required = notesForHand(step, hand);
  return required.length > 0 && required.every(note => heldNotes.has(note));
}
