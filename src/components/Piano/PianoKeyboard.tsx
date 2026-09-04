import { isBlackPianoKey } from '../../lib/pianoVisualization';

interface Props {
  activeNotes: ReadonlySet<number>;
}

export function PianoKeyboard({ activeNotes }: Props) {
  const notes = Array.from({ length: 88 }, (_, index) => index + 21);
  const whiteNotes = notes.filter(note => !isBlackPianoKey(note));
  const whiteIndex = new Map(whiteNotes.map((note, index) => [note, index]));

  return (
    <div className="relative h-52 min-w-[1040px] rounded-b-lg overflow-hidden bg-black border border-white/20">
      <div className="absolute inset-0 flex">
        {whiteNotes.map(note => (
          <div
            key={note}
            aria-label={`MIDI note ${note}`}
            className={`flex-1 border-r border-black/50 rounded-b-sm transition-colors ${
              activeNotes.has(note) ? 'bg-cyan-300' : 'bg-zinc-100'
            }`}
          />
        ))}
      </div>
      {notes.filter(isBlackPianoKey).map(note => {
        const previousWhite = [...whiteNotes]
          .reverse()
          .find(white => white < note);
        if (previousWhite === undefined) return null;
        const index = whiteIndex.get(previousWhite) ?? 0;
        return (
          <div
            key={note}
            aria-label={`MIDI note ${note}`}
            className={`absolute top-0 z-10 h-[62%] rounded-b-sm border border-black transition-colors ${
              activeNotes.has(note) ? 'bg-cyan-400' : 'bg-zinc-900'
            }`}
            style={{
              left: `${((index + 1) / whiteNotes.length) * 100}%`,
              width: `${(0.62 / whiteNotes.length) * 100}%`,
              transform: 'translateX(-50%)',
            }}
          />
        );
      })}
    </div>
  );
}
