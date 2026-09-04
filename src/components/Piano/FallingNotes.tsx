import { useEffect, useState } from 'react';
import type { PianoHand, PracticeStep } from '../../lib/piano';
import {
  buildFallingNotes,
  midiNoteName,
  pianoKeyGeometry,
} from '../../lib/pianoVisualization';

interface Props {
  steps: PracticeStep[];
  stepIndex: number;
  hand: PianoHand;
  tempo: number;
  timelineStartMs: number | null;
  hidden?: boolean;
}

const PIXELS_PER_BEAT = 58;

export function FallingNotes({
  steps,
  stepIndex,
  hand,
  tempo,
  timelineStartMs,
  hidden = false,
}: Props) {
  const [frameTimeMs, setFrameTimeMs] = useState(() => performance.now());

  useEffect(() => {
    if (timelineStartMs === null) return;
    let frame = 0;
    const draw = (now: number) => {
      setFrameTimeMs(now);
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [timelineStartMs]);

  const elapsedBeats =
    timelineStartMs === null
      ? undefined
      : ((frameTimeMs - timelineStartMs) * tempo) / 60_000;
  const notes = hidden
    ? []
    : buildFallingNotes(steps, stepIndex, hand, 8, elapsedBeats);

  return (
    <div
      aria-label="Falling note practice"
      className="relative h-[28rem] min-w-[1040px] overflow-hidden rounded-t-xl border border-b-0 border-white/20 bg-gradient-to-b from-zinc-950 via-slate-950 to-zinc-900"
    >
      <div className="pointer-events-none absolute inset-0 flex opacity-15">
        {Array.from({ length: 52 }, (_, index) => (
          <div key={index} className="flex-1 border-r border-white/30" />
        ))}
      </div>
      {Array.from({ length: 8 }, (_, index) => (
        <div
          key={index}
          className="pointer-events-none absolute inset-x-0 border-t border-white/10"
          style={{ bottom: `${12 + (index + 1) * PIXELS_PER_BEAT}px` }}
        />
      ))}
      {notes.map(item => {
        const geometry = pianoKeyGeometry(item.note);
        if (!geometry) return null;
        return (
          <div
            key={item.id}
            aria-label={`${item.hand} hand ${midiNoteName(item.note)}`}
            className={`absolute flex items-end justify-center rounded-md border px-0.5 pb-1 text-[10px] font-bold text-white shadow-lg ${
              item.hand === 'right'
                ? 'border-cyan-200/80 bg-cyan-500/90 shadow-cyan-500/30'
                : 'border-violet-200/80 bg-violet-500/90 shadow-violet-500/30'
            } ${item.current ? 'ring-2 ring-white/80' : ''}`}
            style={{
              left: `${geometry.leftPercent}%`,
              width: `${geometry.widthPercent}%`,
              bottom: `${12 + item.beatOffset * PIXELS_PER_BEAT}px`,
              height: `${Math.max(24, Math.min(150, item.durationBeats * PIXELS_PER_BEAT))}px`,
            }}
          >
            {geometry.black ? '' : midiNoteName(item.note)}
          </div>
        );
      })}
      <div className="pointer-events-none absolute inset-x-0 bottom-[10px] z-10 h-1 bg-amber-300 shadow-[0_0_16px_rgba(253,224,71,0.9)]" />
      {hidden && (
        <div className="absolute inset-0 grid place-items-center bg-zinc-950/70">
          <p className="rounded-full border border-amber-300/40 bg-amber-300/10 px-5 py-2 text-amber-200">
            Listen, then play the phrase from memory
          </p>
        </div>
      )}
      {!hidden && !notes.length && (
        <div className="absolute inset-0 grid place-items-center text-emerald-300">
          Exercise complete
        </div>
      )}
      <div className="absolute bottom-5 left-4 flex gap-4 text-xs font-medium">
        <span className="text-cyan-300">● Right hand</span>
        <span className="text-violet-300">● Left hand</span>
      </div>
    </div>
  );
}
