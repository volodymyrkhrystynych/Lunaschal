import { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../hooks/api';
import { CuratedTagsSection } from './CuratedTagsSection';
import { ShortcutSettings } from './ShortcutSettings';
import { keyCapture } from '../shortcuts/keymap';

// Maps browser KeyboardEvent.code → evdev keycode name
const CODE_TO_EVDEV: Record<string, string> = (() => {
  const m: Record<string, string> = {
    F1: 'KEY_F1', F2: 'KEY_F2', F3: 'KEY_F3', F4: 'KEY_F4',
    F5: 'KEY_F5', F6: 'KEY_F6', F7: 'KEY_F7', F8: 'KEY_F8',
    F9: 'KEY_F9', F10: 'KEY_F10', F11: 'KEY_F11', F12: 'KEY_F12',
    AltLeft: 'KEY_LEFTALT', AltRight: 'KEY_RIGHTALT',
    ControlLeft: 'KEY_LEFTCTRL', ControlRight: 'KEY_RIGHTCTRL',
    ShiftLeft: 'KEY_LEFTSHIFT', ShiftRight: 'KEY_RIGHTSHIFT',
    MetaLeft: 'KEY_LEFTMETA', MetaRight: 'KEY_RIGHTMETA',
    CapsLock: 'KEY_CAPSLOCK', Insert: 'KEY_INSERT',
    Delete: 'KEY_DELETE', Home: 'KEY_HOME', End: 'KEY_END',
    PageUp: 'KEY_PAGEUP', PageDown: 'KEY_PAGEDOWN',
    ScrollLock: 'KEY_SCROLLLOCK', Pause: 'KEY_PAUSE',
    PrintScreen: 'KEY_SYSRQ', NumLock: 'KEY_NUMLOCK',
    Backquote: 'KEY_GRAVE', Backslash: 'KEY_BACKSLASH',
  };
  for (const c of 'ABCDEFGHIJKLMNOPQRSTUVWXYZ') m[`Key${c}`] = `KEY_${c}`;
  for (const d of '0123456789') m[`Digit${d}`] = `KEY_${d}`;
  return m;
})();

const EVDEV_DISPLAY: Record<string, string> = (() => {
  const m: Record<string, string> = {
    KEY_F1: 'F1', KEY_F2: 'F2', KEY_F3: 'F3', KEY_F4: 'F4',
    KEY_F5: 'F5', KEY_F6: 'F6', KEY_F7: 'F7', KEY_F8: 'F8',
    KEY_F9: 'F9', KEY_F10: 'F10', KEY_F11: 'F11', KEY_F12: 'F12',
    KEY_LEFTALT: 'Left Alt', KEY_RIGHTALT: 'Right Alt',
    KEY_LEFTCTRL: 'Left Ctrl', KEY_RIGHTCTRL: 'Right Ctrl',
    KEY_LEFTSHIFT: 'Left Shift', KEY_RIGHTSHIFT: 'Right Shift',
    KEY_LEFTMETA: 'Left Meta', KEY_RIGHTMETA: 'Right Meta',
    KEY_CAPSLOCK: 'Caps Lock', KEY_INSERT: 'Insert',
    KEY_DELETE: 'Delete', KEY_HOME: 'Home', KEY_END: 'End',
    KEY_PAGEUP: 'Page Up', KEY_PAGEDOWN: 'Page Down',
    KEY_SCROLLLOCK: 'Scroll Lock', KEY_PAUSE: 'Pause',
    KEY_SYSRQ: 'Print Screen', KEY_NUMLOCK: 'Num Lock',
    KEY_GRAVE: 'Backtick', KEY_BACKSLASH: 'Backslash',
  };
  for (const c of 'ABCDEFGHIJKLMNOPQRSTUVWXYZ') m[`KEY_${c}`] = c;
  for (const d of '0123456789') m[`KEY_${d}`] = d;
  return m;
})();

// Modifier keys that can be held as part of a combo
const MODIFIER_CODES = new Set([
  'ControlLeft', 'ControlRight', 'AltLeft', 'AltRight',
  'ShiftLeft', 'ShiftRight', 'MetaLeft', 'MetaRight',
]);

// Canonical sort order for modifiers when building combo strings
const MOD_PRIORITY: Record<string, number> = {
  KEY_LEFTCTRL: 0, KEY_RIGHTCTRL: 1,
  KEY_LEFTALT: 2, KEY_RIGHTALT: 3,
  KEY_LEFTSHIFT: 4, KEY_RIGHTSHIFT: 5,
  KEY_LEFTMETA: 6, KEY_RIGHTMETA: 7,
};

function displayCombo(evdev: string | null | undefined, fallback: string): string {
  if (!evdev) return fallback;
  return evdev.split('+').map(k => EVDEV_DISPLAY[k] ?? k).join(' + ');
}

function KeyRecorder({ value, onChange }: { value: string | null; onChange: (key: string) => void }) {
  const [listening, setListening] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);
  const heldRef = useRef<Set<string>>(new Set());
  const modTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!listening) {
      heldRef.current = new Set();
      return;
    }
    keyCapture.active = true;

    const done = (combo: string) => {
      if (modTimerRef.current) { clearTimeout(modTimerRef.current); modTimerRef.current = null; }
      onChange(combo);
      setListening(false);
      heldRef.current = new Set();
    };

    const handleDown = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();

      if (MODIFIER_CODES.has(e.code)) {
        heldRef.current.add(e.code);
        // Wait briefly for a non-modifier key; if none comes, record modifier(s) alone
        if (modTimerRef.current) clearTimeout(modTimerRef.current);
        modTimerRef.current = setTimeout(() => {
          modTimerRef.current = null;
          const parts = [...heldRef.current]
            .map(m => CODE_TO_EVDEV[m])
            .filter(Boolean) as string[];
          parts.sort((a, b) => (MOD_PRIORITY[a] ?? 99) - (MOD_PRIORITY[b] ?? 99));
          if (parts.length > 0) done(parts.join('+'));
        }, 500);
        return;
      }

      // Non-modifier key: record modifiers currently held + this key immediately
      if (modTimerRef.current) { clearTimeout(modTimerRef.current); modTimerRef.current = null; }
      const evdev = CODE_TO_EVDEV[e.code];
      if (!evdev) return;
      const modParts = [...heldRef.current]
        .map(m => CODE_TO_EVDEV[m])
        .filter(Boolean) as string[];
      modParts.sort((a, b) => (MOD_PRIORITY[a] ?? 99) - (MOD_PRIORITY[b] ?? 99));
      done([...modParts, evdev].join('+'));
    };

    const handleUp = (e: KeyboardEvent) => {
      heldRef.current.delete(e.code);
    };

    window.addEventListener('keydown', handleDown, true);
    window.addEventListener('keyup', handleUp, true);
    return () => {
      window.removeEventListener('keydown', handleDown, true);
      window.removeEventListener('keyup', handleUp, true);
      if (modTimerRef.current) { clearTimeout(modTimerRef.current); modTimerRef.current = null; }
      keyCapture.active = false;
    };
  }, [listening, onChange]);

  useEffect(() => {
    if (listening) ref.current?.focus();
  }, [listening]);

  return (
    <button
      ref={ref}
      onClick={() => setListening(true)}
      onBlur={() => setListening(false)}
      className={`px-3 py-1.5 rounded text-sm border transition-colors ${
        listening
          ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)] animate-pulse'
          : 'border-white/20 bg-white/5 hover:bg-white/10 text-[var(--color-text)]'
      }`}
    >
      {listening ? 'Press a key combo…' : displayCombo(value, 'Not set')}
    </button>
  );
}

function STTStatusSection() {
  const { data, isLoading } = useQuery({ queryKey: ['stt', 'health'], queryFn: api.stt.health, refetchInterval: 5000 });

  const Row = ({ label, ready, detail }: { label: string; ready: boolean; detail: string }) => (
    <div className="flex items-center gap-3">
      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${ready ? 'bg-green-400' : 'bg-red-400'}`} />
      <div>
        <span className="text-sm text-[var(--color-text)]">{label}</span>
        <span className="text-xs text-[var(--color-text-muted)] ml-2">{detail}</span>
      </div>
      <span className={`ml-auto text-xs font-medium ${ready ? 'text-green-400' : 'text-red-400'}`}>
        {ready ? 'ready' : 'unavailable'}
      </span>
    </div>
  );

  return (
    <section className="mb-8">
      <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">Voice Status</h2>
      <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10 space-y-3">
        {isLoading ? (
          <p className="text-sm text-[var(--color-text-muted)]">Checking…</p>
        ) : data ? (
          <>
            <Row label="Speech-to-text" ready={data.stt_ready} detail={`${data.stt_backend} · ${data.stt_model}`} />
            <Row label="Text-to-speech" ready={data.tts_ready} detail={data.tts_backend} />
            {(!data.stt_ready || !data.tts_ready) && (
              <div className="mt-3 pt-3 border-t border-white/10 text-xs text-[var(--color-text-muted)] space-y-1">
                <p>To enable local models: <code>pip install faster-whisper kokoro-onnx</code> (requires GPU)</p>
                <p>To use OpenAI: set <code>STT_BACKEND=openai TTS_BACKEND=openai OPENAI_API_KEY=sk-…</code> and restart</p>
              </div>
            )}
          </>
        ) : (
          <p className="text-sm text-red-400">Could not reach STT service</p>
        )}
      </div>
    </section>
  );
}

function ShortcutsSection() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.settings.get });
  const [pasteKey, setPasteKey] = useState<string | null>(null);
  const [voiceKey, setVoiceKey] = useState<string | null>(null);
  const [journalKey, setJournalKey] = useState<string | null>(null);
  const [commandKey, setCommandKey] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings) {
      setPasteKey(settings.sttPasteKey ?? null);
      setVoiceKey(settings.sttVoiceKey ?? null);
      setJournalKey(settings.sttJournalKey ?? null);
      setCommandKey(settings.sttCommandKey ?? null);
    }
  }, [settings]);

  const save = useMutation({
    mutationFn: () => api.settings.updateShortcuts({
      sttPasteKey: pasteKey ?? undefined,
      sttVoiceKey: voiceKey ?? undefined,
      sttJournalKey: journalKey ?? undefined,
      sttCommandKey: commandKey ?? undefined,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    },
  });

  const togglePipeline = useMutation({
    mutationFn: (enabled: boolean) => api.settings.updateAI({ voicePipelineEnabled: enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const pipelineEnabled = settings?.voicePipelineEnabled ?? true;

  return (
    <section className="mb-8">
      <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">Voice Shortcuts</h2>
      <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10 space-y-4">
        <p className="text-sm text-[var(--color-text-muted)]">
          Click a shortcut button then press the key you want. Restart the STT listener for changes to take effect.
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <p className="text-sm text-[var(--color-text)] mb-1.5">Paste shortcut</p>
            <p className="text-xs text-[var(--color-text-muted)] mb-2">Record → transcribe → paste at cursor</p>
            <KeyRecorder value={pasteKey} onChange={setPasteKey} />
            <p className="text-xs text-[var(--color-text-muted)] mt-1">
              env: <code>STT_PASTE_KEY</code>
            </p>
          </div>
          <div>
            <p className="text-sm text-[var(--color-text)] mb-1.5">Voice shortcut</p>
            <p className="text-xs text-[var(--color-text-muted)] mb-2">
              {pipelineEnabled ? 'Record → transcribe → AI chat → TTS reply' : 'Record → transcribe → paste at cursor'}
            </p>
            <KeyRecorder value={voiceKey} onChange={setVoiceKey} />
            <p className="text-xs text-[var(--color-text-muted)] mt-1">
              env: <code>STT_VOICE_KEY</code>
            </p>
          </div>
          <div>
            <p className="text-sm text-[var(--color-text)] mb-1.5">Journal shortcut</p>
            <p className="text-xs text-[var(--color-text-muted)] mb-2">Record → transcribe → save as journal entry</p>
            <KeyRecorder value={journalKey} onChange={setJournalKey} />
            <p className="text-xs text-[var(--color-text-muted)] mt-1">
              env: <code>STT_JOURNAL_KEY</code>
            </p>
          </div>
          <div>
            <p className="text-sm text-[var(--color-text)] mb-1.5">Command shortcut</p>
            <p className="text-xs text-[var(--color-text-muted)] mb-2">Record → AI parses command → creates todo / event / journal (asks for clarification via TTS)</p>
            <KeyRecorder value={commandKey} onChange={setCommandKey} />
            <p className="text-xs text-[var(--color-text-muted)] mt-1">
              env: <code>STT_COMMAND_KEY</code>
            </p>
          </div>
        </div>

        <label className="flex items-center gap-3 cursor-pointer select-none pt-1">
          <div
            onClick={() => togglePipeline.mutate(!pipelineEnabled)}
            className={`relative w-9 h-5 rounded-full transition-colors ${pipelineEnabled ? 'bg-[var(--color-primary)]' : 'bg-white/20'}`}
          >
            <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${pipelineEnabled ? 'translate-x-4' : 'translate-x-0'}`} />
          </div>
          <span className="text-sm text-[var(--color-text)]">AI chat + TTS reply on voice shortcut</span>
          {!pipelineEnabled && (
            <span className="text-xs text-[var(--color-text-muted)]">(voice shortcut pastes instead)</span>
          )}
        </label>

        <div className="flex items-center gap-3 pt-1">
          <button
            onClick={() => save.mutate()}
            disabled={save.isPending}
            className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50 text-sm"
          >
            {save.isPending ? 'Saving…' : 'Save shortcuts'}
          </button>
          {saved && <span className="text-sm text-green-400">Saved</span>}
        </div>
      </div>
    </section>
  );
}

const VRAM_TOTAL_MB = 8192;
const KOKORO_VRAM_MB = 80;

const RECOMMENDED_CPU_MODELS: { name: string; ramMb: number; note: string }[] = [
  { name: 'phi4-mini',        ramMb:  2350, note: 'fastest on CPU, 12 tok/s' },
  { name: 'llama3.2:3b',      ramMb:  2000, note: 'balanced, fast' },
  { name: 'gemma3:4b',        ramMb:  4300, note: 'best RAM efficiency' },
  { name: 'qwen3:8b',         ramMb:  5800, note: 'strong reasoning' },
  { name: 'llama3.3:8b',      ramMb:  6000, note: 'fast, well-rounded' },
  { name: 'gemma3:12b',       ramMb:  8700, note: 'top on-device quality' },
  { name: 'phi4:14b',         ramMb:  9200, note: 'strong coding & reasoning' },
  { name: 'qwen3:14b',        ramMb: 10500, note: 'multilingual + reasoning' },
  { name: 'gemma3:27b',       ramMb: 17500, note: 'GPU-class quality on CPU' },
  { name: 'qwen3:32b',        ramMb: 22000, note: 'best reasoning <32 GB' },
  { name: 'deepseek-r1:32b',  ramMb: 22000, note: 'best math/reasoning' },
];
const WHISPER_VRAM_TABLE: Record<string, number> = {
  tiny: 1024, base: 1024, small: 2048, medium: 5120, turbo: 6144, 'large-v3': 10240,
};

function VRAMSection() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.settings.get });
  const { data: whisperModels } = useQuery({ queryKey: ['stt', 'whisper-models'], queryFn: api.stt.whisperModels });
  const { data: ollamaModels } = useQuery({
    queryKey: ['settings', 'ollama-models'],
    queryFn: api.settings.ollamaModels,
    enabled: settings?.aiProvider === 'ollama',
  });

  const [saved, setSaved] = useState(false);

  const updateAI = useMutation({
    mutationFn: api.settings.updateAI,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    },
  });

  const reloadStt = useMutation({
    mutationFn: api.stt.reload,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['stt', 'health'] }),
  });

  const activeSttBackend = settings?.sttBackend ?? 'local';
  const activeTtsBackend = settings?.ttsBackend ?? 'local';
  const activeWhisperModel = settings?.whisperModel ?? 'turbo';

  const whisperVram = activeSttBackend === 'local' ? (WHISPER_VRAM_TABLE[activeWhisperModel] ?? 6144) : 0;
  const kokoroVram = activeTtsBackend === 'local' ? KOKORO_VRAM_MB : 0;
  const ollamaVram = settings?.aiProvider === 'ollama' && settings.ollamaModel
    ? (ollamaModels?.find(m => m.name === settings.ollamaModel)?.vramMb ?? 0)
    : 0;
  const totalVram = whisperVram + kokoroVram + ollamaVram;
  const vramPct = Math.min(100, (totalVram / VRAM_TOTAL_MB) * 100);
  const barColor = vramPct > 90 ? 'bg-red-500' : vramPct > 70 ? 'bg-yellow-500' : 'bg-green-500';
  const numColor = vramPct > 90 ? 'text-red-400' : vramPct > 70 ? 'text-yellow-400' : 'text-green-400';

  const setSttBackend = (backend: string) => {
    updateAI.mutate({ sttBackend: backend });
    reloadStt.mutate();
  };

  const setWhisperModel = (model: string) => {
    updateAI.mutate({ whisperModel: model });
    reloadStt.mutate();
  };

  return (
    <section className="mb-8">
      <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">Model & VRAM</h2>
      <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10 space-y-5">
        <div>
          <div className="flex justify-between text-sm mb-1.5">
            <span className="text-[var(--color-text-muted)]">8 GB VRAM budget</span>
            <span className={`font-medium ${numColor}`}>
              {totalVram.toLocaleString()} / {VRAM_TOTAL_MB.toLocaleString()} MB
            </span>
          </div>
          <div className="h-2.5 bg-white/10 rounded-full overflow-hidden">
            <div
              className={`h-full ${barColor} rounded-full transition-all duration-300`}
              style={{ width: `${vramPct}%` }}
            />
          </div>
          <div className="flex flex-wrap gap-x-4 mt-1.5 text-xs text-[var(--color-text-muted)]">
            <span>STT: {activeSttBackend === 'local' ? `${whisperVram} MB` : '0 MB (cloud)'}</span>
            <span>TTS: {activeTtsBackend === 'local' ? `${kokoroVram} MB` : '0 MB (cloud)'}</span>
            {settings?.aiProvider === 'ollama' && (
              <span>LLM: {ollamaVram > 0 ? `~${ollamaVram.toLocaleString()} MB` : 'unknown'}</span>
            )}
          </div>
        </div>

        <div>
          <p className="text-sm font-medium text-[var(--color-text)] mb-2">Speech-to-Text (STT)</p>
          <div className="flex gap-2 mb-2">
            {(['local', 'openai'] as const).map(b => (
              <button
                key={b}
                onClick={() => setSttBackend(b)}
                className={`px-3 py-1.5 rounded text-sm border transition-colors ${
                  activeSttBackend === b
                    ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/15 text-[var(--color-primary)]'
                    : 'border-white/20 bg-white/5 hover:bg-white/10 text-[var(--color-text-muted)]'
                }`}
              >
                {b === 'local' ? 'Local (Whisper)' : 'OpenAI API'}
              </button>
            ))}
          </div>
          {activeSttBackend === 'local' && whisperModels && (
            <select
              value={activeWhisperModel}
              onChange={e => setWhisperModel(e.target.value)}
              className="w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
            >
              {whisperModels.map(m => (
                <option key={m.name} value={m.name}>{m.name} — {m.vramMb} MB</option>
              ))}
            </select>
          )}
        </div>

        <div>
          <p className="text-sm font-medium text-[var(--color-text)] mb-2">Text-to-Speech (TTS)</p>
          <div className="flex gap-2">
            {(['local', 'openai'] as const).map(b => (
              <button
                key={b}
                onClick={() => updateAI.mutate({ ttsBackend: b })}
                className={`px-3 py-1.5 rounded text-sm border transition-colors ${
                  activeTtsBackend === b
                    ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/15 text-[var(--color-primary)]'
                    : 'border-white/20 bg-white/5 hover:bg-white/10 text-[var(--color-text-muted)]'
                }`}
              >
                {b === 'local' ? 'Local (Kokoro ~80 MB)' : 'OpenAI API'}
              </button>
            ))}
          </div>
        </div>

        {settings?.aiProvider === 'ollama' && ollamaModels && ollamaModels.length > 0 && (() => {
          const installedNames = new Set(ollamaModels.map(m => m.name));
          const notInstalled = RECOMMENDED_CPU_MODELS.filter(r => !installedNames.has(r.name));
          return (
            <div>
              <p className="text-sm font-medium text-[var(--color-text)] mb-2">LLM Model (Ollama)</p>
              <select
                value={settings.ollamaModel ?? ''}
                onChange={e => updateAI.mutate({ ollamaModel: e.target.value })}
                className="w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
              >
                <optgroup label="Installed">
                  {ollamaModels.map(m => (
                    <option key={m.name} value={m.name}>{m.name} — {m.vramMb.toLocaleString()} MB</option>
                  ))}
                </optgroup>
                {notInstalled.length > 0 && (
                  <optgroup label="Recommended for CPU (not installed)">
                    {notInstalled.map(m => (
                      <option key={m.name} value={m.name}>{m.name} — ~{m.ramMb.toLocaleString()} MB · {m.note}</option>
                    ))}
                  </optgroup>
                )}
              </select>
            </div>
          );
        })()}

        {settings?.aiProvider === 'ollama' && ollamaModels && ollamaModels.length > 0 && (() => {
          const installedNames = new Set(ollamaModels.map(m => m.name));
          const notInstalled = RECOMMENDED_CPU_MODELS.filter(r => !installedNames.has(r.name));
          return (
            <div>
              <p className="text-sm font-medium text-[var(--color-text)] mb-1">CPU inference model</p>
              <p className="text-xs text-[var(--color-text-muted)] mb-2">Used for background tasks (title & tag generation). Leave unset to use the LLM model above.</p>
              <select
                value={settings.ollamaBgModel ?? ''}
                onChange={e => updateAI.mutate({ ollamaBgModel: e.target.value || null })}
                className="w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
              >
                <option value="">(same as LLM model)</option>
                <optgroup label="Installed">
                  {ollamaModels.map(m => (
                    <option key={m.name} value={m.name}>{m.name} — {m.vramMb.toLocaleString()} MB</option>
                  ))}
                </optgroup>
                {notInstalled.length > 0 && (
                  <optgroup label="Recommended for CPU (not installed)">
                    {notInstalled.map(m => (
                      <option key={m.name} value={m.name}>{m.name} — ~{m.ramMb.toLocaleString()} MB · {m.note}</option>
                    ))}
                  </optgroup>
                )}
              </select>
            </div>
          );
        })()}

        {saved && <p className="text-xs text-green-400">Saved</p>}
      </div>
    </section>
  );
}

type Provider = 'openai' | 'gemini' | 'ollama';

function NetworkSection() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.settings.get });

  const regenerate = useMutation({
    mutationFn: api.settings.regenerateCode,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const toggleSleep = useMutation({
    mutationFn: (enabled: boolean) => api.settings.updateAI({ preventSleep: enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const preventSleep = settings?.preventSleep ?? false;

  const logout = useMutation({
    mutationFn: api.auth.logout,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['auth', 'status'] }),
  });

  const origin = window.location.origin;

  return (
    <section className="mb-8">
      <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">Network Access</h2>
      <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10 space-y-4">
        <div>
          <p className="text-sm text-[var(--color-text-muted)] mb-1">Connect from your laptop at:</p>
          <code className="text-sm text-[var(--color-primary)]">{origin}</code>
        </div>
        <div>
          <p className="text-sm text-[var(--color-text-muted)] mb-2">Display code (second factor):</p>
          <div className="flex items-center gap-4">
            <span className="text-4xl font-mono tracking-[0.3em] text-[var(--color-text)]">
              {settings?.networkCode ?? '------'}
            </span>
            <button
              onClick={() => regenerate.mutate()}
              disabled={regenerate.isPending}
              className="px-3 py-1 text-sm bg-white/10 hover:bg-white/20 text-[var(--color-text)] rounded disabled:opacity-50 transition-colors"
            >
              {regenerate.isPending ? 'Regenerating…' : 'Regenerate'}
            </button>
          </div>
          <p className="text-xs text-[var(--color-text-muted)] mt-2">
            Laptop sign-in requires this code plus <code>LUNASCHAL_PASSWORD</code>.
            Regenerate after each remote session.
          </p>
        </div>
        <label className="flex items-center gap-3 cursor-pointer select-none pt-2 border-t border-white/10">
          <div
            onClick={() => toggleSleep.mutate(!preventSleep)}
            className={`relative w-9 h-5 rounded-full transition-colors ${preventSleep ? 'bg-[var(--color-primary)]' : 'bg-white/20'}`}
          >
            <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${preventSleep ? 'translate-x-4' : 'translate-x-0'}`} />
          </div>
          <div>
            <span className="text-sm text-[var(--color-text)]">Prevent sleep</span>
            <p className="text-xs text-[var(--color-text-muted)]">Keep the server awake while running</p>
          </div>
        </label>
        <div className="pt-2 border-t border-white/10">
          <button
            onClick={() => logout.mutate()}
            disabled={logout.isPending}
            className="px-3 py-1 text-sm text-red-400 hover:text-red-300 disabled:opacity-50"
          >
            Sign out all sessions
          </button>
        </div>
      </div>
    </section>
  );
}

function KnowledgeBaseSection() {
  const [syncProgress, setSyncProgress] = useState<string | null>(null);

  const { data: ragConfigured } = useQuery({ queryKey: ['rag', 'configured'], queryFn: api.rag.isConfigured });
  const { data: stats } = useQuery({ queryKey: ['rag', 'stats'], queryFn: api.rag.getStats });

  const syncAll = useMutation({
    mutationFn: api.rag.syncAll,
    onMutate: () => setSyncProgress('Starting sync...'),
    onSuccess: (result) => {
      setSyncProgress(`Synced ${result.synced} entries (${result.chunks} chunks)`);
      setTimeout(() => setSyncProgress(null), 5000);
    },
    onError: (error: Error) => setSyncProgress(`Error: ${error.message}`),
  });

  return (
    <section className="mb-8">
      <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">Knowledge Base</h2>
      <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
        <p className="text-sm text-[var(--color-text-muted)] mb-4">
          The knowledge base uses AI embeddings to enable semantic search across your journal entries.
          This allows the AI to find relevant context from your notes when chatting.
        </p>
        {!ragConfigured ? (
          <div className="bg-yellow-900/30 border border-yellow-600/50 rounded-lg p-3 text-yellow-200 text-sm">
            Embeddings require OpenAI or Google API key. Configure one above to enable semantic search.
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div className="bg-white/5 rounded-lg p-3">
                <div className="text-2xl font-bold text-[var(--color-text)]">{stats?.totalJournals || 0}</div>
                <div className="text-sm text-[var(--color-text-muted)]">Journal Entries</div>
              </div>
              <div className="bg-white/5 rounded-lg p-3">
                <div className="text-2xl font-bold text-green-400">{stats?.isConfigured ? 'Active' : 'Inactive'}</div>
                <div className="text-sm text-[var(--color-text-muted)]">Embedding Status</div>
              </div>
            </div>
            <div className="flex items-center gap-4">
              <button onClick={() => syncAll.mutate()} disabled={syncAll.isPending}
                className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">
                {syncAll.isPending ? 'Syncing...' : 'Rebuild Knowledge Base'}
              </button>
              {syncProgress && <span className="text-sm text-[var(--color-text-muted)]">{syncProgress}</span>}
            </div>
            <p className="text-xs text-[var(--color-text-muted)] mt-3">
              New journal entries are automatically indexed. Use "Rebuild" to re-index all entries after changing AI providers.
            </p>
          </>
        )}
      </div>
    </section>
  );
}

function FanficCookieRow({ domain, hasCookie, updatedAt }: { domain: string; hasCookie: boolean; updatedAt: string | null }) {
  const [value, setValue] = useState('');
  const queryClient = useQueryClient();

  const save = useMutation({
    mutationFn: (cookie: string) => api.fanfic.cookies.put(domain, cookie),
    onSuccess: () => {
      setValue('');
      queryClient.invalidateQueries({ queryKey: ['fanfic', 'cookies'] });
    },
  });

  return (
    <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-medium text-[var(--color-text)]">{domain}</h3>
        {hasCookie ? (
          <span className="text-xs text-green-400">
            cookie set{updatedAt && ` · ${new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(new Date(updatedAt))}`}
          </span>
        ) : (
          <span className="text-xs text-[var(--color-text-muted)]">no cookie</span>
        )}
      </div>
      <div className="flex gap-2">
        <input type="text" value={value} onChange={(e) => setValue(e.target.value)}
          spellCheck={false} autoComplete="off"
          placeholder={hasCookie ? 'paste a new cookie to replace the stored one' : 'xf_user=...; xf_session=...; cf_clearance=...'}
          className="flex-1 bg-transparent font-mono text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]" />
        <button onClick={() => save.mutate(value.trim())}
          disabled={!value.trim() || save.isPending}
          className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">Save</button>
        {hasCookie && (
          <button onClick={() => save.mutate('')} disabled={save.isPending}
            className="px-3 py-2 text-sm text-red-400 hover:text-red-300 disabled:opacity-50">Clear</button>
        )}
      </div>
    </div>
  );
}

function FanficCookiesSection() {
  const { data: cookies } = useQuery({ queryKey: ['fanfic', 'cookies'], queryFn: api.fanfic.cookies.list });

  return (
    <section className="mb-8">
      <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">Fanfic Site Cookies</h2>
      <p className="text-sm text-[var(--color-text-muted)] mb-4">
        Needed for login-gated fics (e.g. Questionable Questing NSFW sections). Log in to the site
        in your browser, open DevTools (<code>F12</code>) → <strong>Network</strong> tab, reload
        the page, then right-click the first request → <strong>Copy Value → Copy Request
        Headers</strong> and paste the whole thing below — the <code>Cookie</code> line is
        extracted automatically. The Cookies tab's <strong>Copy All</strong> JSON, a "Copy as
        cURL" command, or a bare cookie string all work too.
      </p>
      <div className="space-y-4">
        {cookies?.map((c) => (
          <FanficCookieRow key={c.domain} domain={c.domain} hasCookie={c.hasCookie} updatedAt={c.updatedAt} />
        ))}
      </div>
    </section>
  );
}

export function Settings() {
  const [activeTab, setActiveTab] = useState<'general' | 'tags' | 'shortcuts'>('general');
  const [openaiKey, setOpenaiKey] = useState('');
  const [googleKey, setGoogleKey] = useState('');
  const [ollamaUrl, setOllamaUrl] = useState('http://localhost:11434');
  const [ollamaModel, setOllamaModel] = useState('llama3.2');
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const queryClient = useQueryClient();

  const { data: settings, isLoading } = useQuery({ queryKey: ['settings'], queryFn: api.settings.get });

  useEffect(() => {
    if (settings) {
      setOllamaUrl(settings.ollamaUrl || 'http://localhost:11434');
      setOllamaModel(settings.ollamaModel || 'llama3.2');
    }
  }, [settings]);

  const updateAI = useMutation({
    mutationFn: api.settings.updateAI,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      setMessage({ type: 'success', text: 'Settings saved successfully' });
      setTimeout(() => setMessage(null), 3000);
    },
    onError: (error: Error) => setMessage({ type: 'error', text: error.message }),
  });

  const providers: { id: Provider; label: string; subtitle: string; status: string }[] = [
    { id: 'openai', label: 'OpenAI', subtitle: 'GPT-4o and other OpenAI models', status: settings?.hasOpenaiKey ? '✓ API key configured' : '✗ No API key' },
    { id: 'gemini', label: 'Google Gemini', subtitle: 'Gemini 2.0 Flash and other models', status: settings?.hasGoogleKey ? '✓ API key configured' : '✗ No API key' },
    { id: 'ollama', label: 'Ollama (Local)', subtitle: 'Run AI models locally', status: `URL: ${settings?.ollamaUrl || 'http://localhost:11434'}` },
  ];

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-[var(--color-text-muted)]">Loading...</div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-4">
      <div className="flex items-center gap-4 mb-6">
        <h1 className="text-2xl font-semibold text-[var(--color-text)]">Settings</h1>
        <div className="flex gap-1 ml-2">
          {(['general', 'tags', 'shortcuts'] as const).map(tab => (
            <button key={tab} onClick={() => setActiveTab(tab)}
              className={`px-4 py-1.5 rounded text-sm transition-colors ${
                activeTab === tab
                  ? 'bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40'
                  : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
              }`}>
              {tab === 'general' ? 'General' : tab === 'tags' ? 'Tags' : 'Shortcuts'}
            </button>
          ))}
        </div>
      </div>

      {activeTab === 'tags' ? (
        <CuratedTagsSection />
      ) : activeTab === 'shortcuts' ? (
        <ShortcutSettings />
      ) : (
      <>

      {message && (
        <div className={`mb-4 p-3 rounded-lg ${message.type === 'success' ? 'bg-green-900/30 border border-green-600/50 text-green-200' : 'bg-red-900/30 border border-red-600/50 text-red-200'}`}>
          {message.text}
        </div>
      )}

      <section className="mb-8">
        <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">AI Provider</h2>
        <div className="grid gap-4 md:grid-cols-3">
          {providers.map((p) => (
            <div key={p.id} onClick={() => updateAI.mutate({ aiProvider: p.id })}
              className={`p-4 bg-[var(--color-surface)] rounded-lg border transition-colors cursor-pointer ${settings?.aiProvider === p.id ? 'border-[var(--color-primary)]' : 'border-white/10 hover:border-white/20'}`}>
              <div className="flex items-center gap-2 mb-2">
                <div className={`w-3 h-3 rounded-full ${settings?.aiProvider === p.id ? 'bg-[var(--color-primary)]' : 'bg-white/20'}`} />
                <h3 className="font-medium text-[var(--color-text)]">{p.label}</h3>
              </div>
              <p className="text-sm text-[var(--color-text-muted)] mb-3">{p.subtitle}</p>
              <div className="text-xs text-[var(--color-text-muted)]">{p.status}</div>
            </div>
          ))}
        </div>
      </section>

      <VRAMSection />

      <section className="mb-8">
        <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">API Keys</h2>
        <div className="space-y-4">
          <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
            <h3 className="font-medium text-[var(--color-text)] mb-2">OpenAI API Key</h3>
            <div className="flex gap-2">
              <input type="password" value={openaiKey} onChange={(e) => setOpenaiKey(e.target.value)}
                placeholder={settings?.hasOpenaiKey ? '••••••••••••••••' : 'sk-...'}
                className="flex-1 bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]" />
              <button onClick={() => { updateAI.mutate({ openaiApiKey: openaiKey, aiProvider: 'openai' }); setOpenaiKey(''); }}
                disabled={!openaiKey.trim() || updateAI.isPending}
                className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">Save</button>
            </div>
          </div>

          <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
            <h3 className="font-medium text-[var(--color-text)] mb-2">Google API Key</h3>
            <div className="flex gap-2">
              <input type="password" value={googleKey} onChange={(e) => setGoogleKey(e.target.value)}
                placeholder={settings?.hasGoogleKey ? '••••••••••••••••' : 'AIza...'}
                className="flex-1 bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]" />
              <button onClick={() => { updateAI.mutate({ googleApiKey: googleKey, aiProvider: 'gemini' }); setGoogleKey(''); }}
                disabled={!googleKey.trim() || updateAI.isPending}
                className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">Save</button>
            </div>
          </div>

          <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
            <h3 className="font-medium text-[var(--color-text)] mb-2">Ollama Configuration</h3>
            <div className="space-y-3">
              <div>
                <label className="text-sm text-[var(--color-text-muted)]">Server URL</label>
                <input type="text" value={ollamaUrl} onChange={(e) => setOllamaUrl(e.target.value)}
                  placeholder="http://localhost:11434"
                  className="w-full bg-transparent text-[var(--color-text)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]" />
              </div>
              <div>
                <label className="text-sm text-[var(--color-text-muted)]">Model</label>
                <input type="text" value={ollamaModel} onChange={(e) => setOllamaModel(e.target.value)} placeholder="llama3.2"
                  className="w-full bg-transparent text-[var(--color-text)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]" />
              </div>
              <button onClick={() => updateAI.mutate({ ollamaUrl, ollamaModel, aiProvider: 'ollama' })} disabled={updateAI.isPending}
                className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">
                Save Ollama Settings
              </button>
            </div>
          </div>
        </div>
      </section>

      <STTStatusSection />

      <ShortcutsSection />

      <KnowledgeBaseSection />

      <FanficCookiesSection />

      {settings?.networkMode && <NetworkSection />}

      <section>
        <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">About</h2>
        <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
          <p className="text-[var(--color-text)]">Lunaschal v0.1.0</p>
          <p className="text-sm text-[var(--color-text-muted)] mt-1">A privacy-first, self-hosted personal AI knowledge assistant.</p>
        </div>
      </section>
      </>
      )}
    </div>
  );
}
