import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../hooks/api';
import { desktopApi, type MidiDevice } from '../../lib/desktopBridge';
import {
  notesForHand,
  parsePracticeSteps,
  stepIsComplete,
  type PianoHand,
  type PianoPiece,
} from '../../lib/piano';
import { renderMusicXml } from '../../lib/verovio';
import { PianoArchive } from './PianoArchive';
import { PianoKeyboard } from './PianoKeyboard';

export function Piano() {
  const [section, setSection] = useState<'practice' | 'archive'>('practice');
  const [devices, setDevices] = useState<MidiDevice[]>([]);
  const [deviceId, setDeviceId] = useState('');
  const [connected, setConnected] = useState(false);
  const [activeNotes, setActiveNotes] = useState<Set<number>>(new Set());
  const activeRef = useRef(activeNotes);
  const [sustain, setSustain] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pieces, setPieces] = useState<PianoPiece[]>([]);
  const [piece, setPiece] = useState<PianoPiece | null>(null);
  const [score, setScore] = useState('');
  const [scorePages, setScorePages] = useState<string[]>([]);
  const [loadingScore, setLoadingScore] = useState(false);
  const [hand, setHand] = useState<PianoHand>('both');
  const [loopStart, setLoopStart] = useState(1);
  const [loopEnd, setLoopEnd] = useState(1);
  const [stepIndex, setStepIndex] = useState(0);
  const stepIndexRef = useRef(0);
  const [practicing, setPracticing] = useState(false);
  const practicingRef = useRef(false);
  const [wrongNotes, setWrongNotes] = useState(0);

  const allSteps = useMemo(
    () => (score ? parsePracticeSteps(score) : []),
    [score]
  );
  const practiceSteps = useMemo(
    () =>
      allSteps.filter(
        step =>
          step.measure >= loopStart &&
          step.measure <= loopEnd &&
          notesForHand(step, hand).length > 0
      ),
    [allSteps, hand, loopEnd, loopStart]
  );
  const stepsRef = useRef(practiceSteps);
  const handRef = useRef(hand);
  stepsRef.current = practiceSteps;
  handRef.current = hand;
  stepIndexRef.current = stepIndex;
  practicingRef.current = practicing;
  activeRef.current = activeNotes;
  const currentStep = practiceSteps[stepIndex] ?? null;

  const refreshDevices = async () => {
    const found = (await desktopApi()?.midi_devices()) ?? [];
    setDevices(found);
    setDeviceId(current =>
      found.some(device => device.id === current)
        ? current
        : (found[0]?.id ?? '')
    );
  };

  const refreshPieces = async () => {
    try {
      setPieces(await api.piano.list());
    } catch (cause) {
      setError(errorMessage(cause, 'Could not load scores.'));
    }
  };

  useEffect(() => {
    void refreshDevices();
    void refreshPieces();
  }, []);

  useEffect(() => {
    if (!connected) return;
    const timer = window.setInterval(async () => {
      const result = await desktopApi()?.midi_poll();
      if (!result) return;
      setConnected(result.connected);
      for (const event of result.events) {
        if (event.kind === 'error') {
          setError(event.message ?? 'The MIDI device disconnected.');
          continue;
        }
        if (event.kind === 'sustain') {
          setSustain((event.value ?? 0) >= 64);
          continue;
        }
        if (event.note === undefined) continue;
        const held = new Set(activeRef.current);
        if (event.kind === 'noteOn') held.add(event.note);
        else held.delete(event.note);
        activeRef.current = held;
        setActiveNotes(held);

        if (event.kind !== 'noteOn' || !practicingRef.current) continue;
        const step = stepsRef.current[stepIndexRef.current];
        if (!step) continue;
        if (stepIsComplete(step, handRef.current, held)) {
          const next = stepIndexRef.current + 1;
          stepIndexRef.current = next;
          setStepIndex(next);
          if (next >= stepsRef.current.length) {
            practicingRef.current = false;
            setPracticing(false);
          }
        } else if (!notesForHand(step, handRef.current).includes(event.note)) {
          setWrongNotes(count => count + 1);
        }
      }
    }, 16);
    return () => window.clearInterval(timer);
  }, [connected]);

  useEffect(
    () => () => {
      void desktopApi()?.midi_close();
    },
    []
  );

  const connect = async () => {
    if (!deviceId) return;
    setError(null);
    const result = await desktopApi()?.midi_open(deviceId);
    if (!result?.ok) {
      setError(result?.error ?? 'Could not open the MIDI device.');
    } else {
      setConnected(true);
    }
  };

  const openPiece = async (selected: PianoPiece) => {
    setPiece(selected);
    setLoadingScore(true);
    setPracticing(false);
    setStepIndex(0);
    setError(null);
    try {
      const xml = await api.piano.score(selected.id);
      const steps = parsePracticeSteps(xml);
      const measures = steps.map(step => step.measure);
      setScore(xml);
      setLoopStart(Math.min(...measures, 1));
      setLoopEnd(Math.max(...measures, 1));
      setScorePages(await renderMusicXml(xml));
    } catch (cause) {
      setError(errorMessage(cause, 'Could not open the score.'));
    } finally {
      setLoadingScore(false);
    }
  };

  const importScore = async (file: File) => {
    setError(null);
    try {
      const imported = await api.piano.import(file);
      await refreshPieces();
      await openPiece(imported);
    } catch (cause) {
      setError(errorMessage(cause, 'Could not import the score.'));
    }
  };

  const removePiece = async (selected: PianoPiece) => {
    if (!window.confirm(`Delete “${selected.title}” from the Piano library?`))
      return;
    try {
      await api.piano.remove(selected.id);
      if (piece?.id === selected.id) {
        setPiece(null);
        setScore('');
        setScorePages([]);
      }
      await refreshPieces();
    } catch (cause) {
      setError(errorMessage(cause, 'Could not delete the score.'));
    }
  };

  const startPractice = () => {
    setWrongNotes(0);
    setStepIndex(0);
    stepIndexRef.current = 0;
    setPracticing(practiceSteps.length > 0);
  };

  return (
    <section className="flex flex-1 overflow-hidden text-[var(--color-text)]">
      <aside className="w-72 shrink-0 overflow-y-auto border-r border-white/10 bg-[var(--color-surface)] p-4">
        <h2 className="text-xl font-semibold">Piano</h2>
        <div className="mt-4 grid grid-cols-2 rounded border border-white/10 p-1 text-sm">
          {(['practice', 'archive'] as const).map(value => (
            <button
              key={value}
              type="button"
              onClick={() => setSection(value)}
              className={`rounded px-2 py-1.5 capitalize ${
                section === value
                  ? 'bg-[var(--color-primary)] text-white'
                  : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
              }`}
            >
              {value}
            </button>
          ))}
        </div>
        {section === 'practice' ? (
          <>
            <label className="mt-4 block cursor-pointer rounded bg-[var(--color-primary)] px-4 py-2 text-center text-white">
              Import sheet music
              <input
                type="file"
                accept=".musicxml,.xml,.mxl"
                className="hidden"
                onChange={event => {
                  const file = event.target.files?.[0];
                  if (file) void importScore(file);
                  event.target.value = '';
                }}
              />
            </label>
            <p className="mt-2 text-xs text-[var(--color-text-muted)]">
              MusicXML, XML, or compressed MXL
            </p>
            <div className="mt-5 space-y-2">
              {!pieces.length && (
                <p className="text-sm text-[var(--color-text-muted)]">
                  No pieces imported yet.
                </p>
              )}
              {pieces.map(item => (
                <div
                  key={item.id}
                  className={`w-full rounded border p-3 text-left ${
                    piece?.id === item.id
                      ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/10'
                      : 'border-white/10 hover:border-white/30'
                  }`}
                >
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => void openPiece(item)}
                      className="min-w-0 flex-1 text-left"
                    >
                      <span className="block font-medium">{item.title}</span>
                      {item.composer && (
                        <span className="block text-xs text-[var(--color-text-muted)]">
                          {item.composer}
                        </span>
                      )}
                    </button>
                    <button
                      type="button"
                      aria-label={`Delete ${item.title}`}
                      title="Delete score"
                      onClick={() => void removePiece(item)}
                      className="self-start rounded px-2 py-1 text-[var(--color-text-muted)] hover:bg-red-500/10 hover:text-red-300"
                    >
                      ×
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </>
        ) : (
          <p className="mt-4 text-sm text-[var(--color-text-muted)]">
            Browse large collections on the external backup drive. Star a
            MusicXML score to add it here for practice.
          </p>
        )}
      </aside>

      <div className="flex-1 space-y-5 overflow-auto p-5">
        {section === 'archive' ? (
          <PianoArchive onLibraryChanged={refreshPieces} />
        ) : (
          <>
            <MidiControls
              devices={devices}
              deviceId={deviceId}
              connected={connected}
              sustain={sustain}
              onDevice={setDeviceId}
              onConnect={connect}
              onDisconnect={async () => {
                await desktopApi()?.midi_close();
                setConnected(false);
              }}
              onRefresh={refreshDevices}
            />
            {error && (
              <div
                role="alert"
                className="rounded border border-red-500/50 bg-red-500/10 p-3 text-red-300"
              >
                {error}
              </div>
            )}
            {piece ? (
              <>
                <PracticeControls
                  piece={piece}
                  hand={hand}
                  loopStart={loopStart}
                  loopEnd={loopEnd}
                  connected={connected}
                  canStart={practiceSteps.length > 0}
                  onHand={value => {
                    setHand(value);
                    setStepIndex(0);
                  }}
                  onLoopStart={setLoopStart}
                  onLoopEnd={setLoopEnd}
                  onStart={startPractice}
                />
                <div className="sticky top-0 z-20 flex flex-wrap items-center gap-4 rounded-lg border border-cyan-400/30 bg-zinc-950/95 p-3 shadow-lg">
                  <strong>
                    {practicing
                      ? 'Your turn'
                      : stepIndex >= practiceSteps.length &&
                          practiceSteps.length
                        ? 'Complete'
                        : 'Ready'}
                  </strong>
                  {currentStep && (
                    <span>
                      Measure {currentStep.measure}, beat{' '}
                      {formatBeat(currentStep.beat)}
                    </span>
                  )}
                  {currentStep && (
                    <span className="text-cyan-300">
                      Play MIDI {notesForHand(currentStep, hand).join(' + ')}
                    </span>
                  )}
                  <span className="text-sm text-[var(--color-text-muted)]">
                    Step {Math.min(stepIndex + 1, practiceSteps.length)} /{' '}
                    {practiceSteps.length} · Wrong notes {wrongNotes}
                  </span>
                </div>
                <div className="overflow-x-auto">
                  <PianoKeyboard activeNotes={activeNotes} />
                </div>
                <div className="space-y-4">
                  {loadingScore && (
                    <p className="text-[var(--color-text-muted)]">
                      Engraving score…
                    </p>
                  )}
                  {scorePages.map((svg, index) => (
                    <div
                      key={index}
                      className="mx-auto max-w-5xl overflow-hidden rounded bg-white p-3"
                      dangerouslySetInnerHTML={{ __html: svg }}
                    />
                  ))}
                </div>
              </>
            ) : (
              <div className="rounded-lg border border-dashed border-white/20 p-12 text-center text-[var(--color-text-muted)]">
                Import a MusicXML score to begin learning a piece.
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}

function MidiControls(props: {
  devices: MidiDevice[];
  deviceId: string;
  connected: boolean;
  sustain: boolean;
  onDevice: (id: string) => void;
  onConnect: () => Promise<void>;
  onDisconnect: () => Promise<void>;
  onRefresh: () => Promise<void>;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-white/10 bg-[var(--color-surface)] p-4">
      <select
        aria-label="MIDI input"
        value={props.deviceId}
        onChange={event => props.onDevice(event.target.value)}
        disabled={props.connected}
        className="min-w-56 rounded border border-white/20 bg-[var(--color-bg)] px-3 py-2"
      >
        {!props.devices.length && (
          <option value="">No MIDI devices found</option>
        )}
        {props.devices.map(device => (
          <option key={device.id} value={device.id}>
            {device.name} ({device.id})
          </option>
        ))}
      </select>
      <button
        type="button"
        disabled={!props.deviceId}
        onClick={() =>
          void (props.connected ? props.onDisconnect() : props.onConnect())
        }
        className="rounded bg-[var(--color-primary)] px-4 py-2 text-white disabled:opacity-40"
      >
        {props.connected ? 'Disconnect' : 'Connect MIDI'}
      </button>
      <button
        type="button"
        onClick={() => void props.onRefresh()}
        className="rounded border border-white/20 px-3 py-2"
      >
        Refresh
      </button>
      <span className="text-sm text-[var(--color-text-muted)]">
        {props.connected ? 'Connected' : 'Disconnected'} · Sustain{' '}
        {props.sustain ? 'on' : 'off'}
      </span>
    </div>
  );
}

function PracticeControls(props: {
  piece: PianoPiece;
  hand: PianoHand;
  loopStart: number;
  loopEnd: number;
  connected: boolean;
  canStart: boolean;
  onHand: (hand: PianoHand) => void;
  onLoopStart: (measure: number) => void;
  onLoopEnd: (measure: number) => void;
  onStart: () => void;
}) {
  return (
    <div className="flex flex-wrap items-end gap-4 rounded-lg border border-white/10 bg-[var(--color-surface)] p-4">
      <div className="mr-auto">
        <h3 className="text-xl font-semibold">{props.piece.title}</h3>
        <p className="text-sm text-[var(--color-text-muted)]">
          {props.piece.composer ?? 'Unknown composer'}
        </p>
      </div>
      <label className="text-sm">
        Hand
        <select
          value={props.hand}
          onChange={event => props.onHand(event.target.value as PianoHand)}
          className="ml-2 rounded border border-white/20 bg-[var(--color-bg)] px-2 py-1"
        >
          <option value="both">Both</option>
          <option value="right">Right</option>
          <option value="left">Left</option>
        </select>
      </label>
      <label className="text-sm">
        Measures
        <input
          aria-label="First measure"
          type="number"
          min="1"
          value={props.loopStart}
          onChange={event => props.onLoopStart(Number(event.target.value))}
          className="ml-2 w-16 rounded border border-white/20 bg-[var(--color-bg)] px-2 py-1"
        />
        <span className="mx-1">–</span>
        <input
          aria-label="Last measure"
          type="number"
          min={props.loopStart}
          value={props.loopEnd}
          onChange={event => props.onLoopEnd(Number(event.target.value))}
          className="w-16 rounded border border-white/20 bg-[var(--color-bg)] px-2 py-1"
        />
      </label>
      <button
        type="button"
        disabled={!props.connected || !props.canStart}
        onClick={props.onStart}
        className="rounded bg-emerald-600 px-4 py-2 text-white disabled:opacity-40"
      >
        Start practice
      </button>
    </div>
  );
}

function errorMessage(cause: unknown, fallback: string): string {
  return cause instanceof Error ? cause.message : fallback;
}

function formatBeat(beat: number): string {
  return Number.isInteger(beat)
    ? String(beat)
    : beat.toFixed(2).replace(/0+$/, '');
}
