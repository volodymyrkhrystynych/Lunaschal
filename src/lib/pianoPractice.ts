import type { PianoHand, PracticeStep } from './piano';
import { notesForHand } from './piano';

export interface CapturedMidiEvent {
  kind: 'noteOn' | 'noteOff';
  note: number;
  timestampMs: number;
  velocity?: number;
}

export interface PracticeMetrics {
  onsetAccuracy: number;
  durationAccuracy: number | null;
  tempoStability: number;
  velocityEvenness: number | null;
  achievedTempo: number | null;
}

const clampScore = (value: number) =>
  Math.max(0, Math.min(100, Math.round(value)));

export function scorePerformance(
  steps: PracticeStep[],
  hand: PianoHand,
  events: CapturedMidiEvent[],
  tempo: number
): PracticeMetrics {
  const ons = events.filter(event => event.kind === 'noteOn');
  if (!steps.length || !ons.length)
    return {
      onsetAccuracy: 0,
      durationAccuracy: null,
      tempoStability: 0,
      velocityEvenness: null,
      achievedTempo: null,
    };
  const beatMs = 60_000 / tempo;
  const origin = ons[0].timestampMs;
  let beat = 0;
  const expected = steps.map(step => {
    const value = { time: origin + beat * beatMs, step };
    beat += step.durationBeats;
    return value;
  });
  const used = new Set<number>();
  const matched = expected.map(({ step }, index) => {
    const required = notesForHand(step, hand);
    let bestIndex = -1;
    ons.forEach((event, eventIndex) => {
      if (used.has(eventIndex) || !required.includes(event.note)) return;
      if (
        bestIndex < 0 ||
        Math.abs(event.timestampMs - expected[index].time) <
          Math.abs(ons[bestIndex].timestampMs - expected[index].time)
      )
        bestIndex = eventIndex;
    });
    if (bestIndex < 0) return undefined;
    used.add(bestIndex);
    return ons[bestIndex];
  });
  const onsetErrors = matched.map((event, index) =>
    event ? Math.abs(event.timestampMs - expected[index].time) / beatMs : 1
  );
  const onsetAccuracy = clampScore(
    100 *
      (1 -
        onsetErrors.reduce((a, b) => a + Math.min(1, b), 0) /
          onsetErrors.length)
  );
  const intervals = matched
    .slice(1)
    .flatMap((event, index) =>
      event && matched[index]
        ? [event.timestampMs - matched[index]!.timestampMs]
        : []
    );
  const expectedIntervals = steps
    .slice(0, -1)
    .map(step => step.durationBeats * beatMs);
  const stabilityErrors = intervals.map(
    (value, index) => Math.abs(value - expectedIntervals[index]) / beatMs
  );
  const tempoStability = stabilityErrors.length
    ? clampScore(
        100 *
          (1 -
            stabilityErrors.reduce((a, b) => a + Math.min(1, b), 0) /
              stabilityErrors.length)
      )
    : 100;
  const releases: number[] = [];
  matched.forEach((on, index) => {
    if (!on) return;
    const off = events.find(
      event =>
        event.kind === 'noteOff' &&
        event.note === on.note &&
        event.timestampMs >= on.timestampMs
    );
    if (off)
      releases.push(
        Math.abs(
          off.timestampMs - on.timestampMs - steps[index].durationBeats * beatMs
        ) / beatMs
      );
  });
  const velocities = ons
    .map(event => event.velocity)
    .filter((value): value is number => value !== undefined && value > 0);
  const mean =
    velocities.reduce((sum, value) => sum + value, 0) /
    (velocities.length || 1);
  const deviation =
    velocities.reduce((sum, value) => sum + Math.abs(value - mean), 0) /
    (velocities.length || 1);
  const elapsed =
    matched.at(-1)?.timestampMs != null && matched[0]?.timestampMs != null
      ? matched.at(-1)!.timestampMs - matched[0]!.timestampMs
      : 0;
  const beatsElapsed = steps
    .slice(0, -1)
    .reduce((sum, step) => sum + step.durationBeats, 0);
  return {
    onsetAccuracy,
    durationAccuracy: releases.length
      ? clampScore(
          100 *
            (1 -
              releases.reduce((a, b) => a + Math.min(1, b), 0) /
                releases.length)
        )
      : null,
    tempoStability,
    velocityEvenness:
      velocities.length > 1
        ? clampScore(100 * (1 - deviation / Math.max(mean, 1)))
        : null,
    achievedTempo:
      elapsed > 0 ? Math.round((60_000 * beatsElapsed) / elapsed) : null,
  };
}

/** Deterministic pitch-class policy for ii-V-I: inversions, optional fifths. */
export function jazzVoicingAccepted(
  stepIndex: number,
  played: ReadonlySet<number>,
  tonic: number
): boolean {
  const policies = [
    { required: [2, 5, 0], optional: [9] }, // ii: root, third, seventh; fifth optional
    { required: [7, 11, 5], optional: [2] }, // V7
    { required: [0, 4, 11], optional: [7, 2, 9] }, // Imaj7, extensions permitted
  ];
  const policy = policies[stepIndex % 3];
  const pcs = new Set([...played].map(note => (note - tonic + 120) % 12));
  return (
    policy.required.every(pc => pcs.has(pc)) &&
    [...pcs].every(
      pc => policy.required.includes(pc) || policy.optional.includes(pc)
    )
  );
}
