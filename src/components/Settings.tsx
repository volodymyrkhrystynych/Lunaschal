import { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../hooks/api';

// Maps browser KeyboardEvent.code → evdev keycode name
const CODE_TO_EVDEV: Record<string, string> = {
  F1: 'KEY_F1', F2: 'KEY_F2', F3: 'KEY_F3', F4: 'KEY_F4',
  F5: 'KEY_F5', F6: 'KEY_F6', F7: 'KEY_F7', F8: 'KEY_F8',
  F9: 'KEY_F9', F10: 'KEY_F10', F11: 'KEY_F11', F12: 'KEY_F12',
  AltLeft: 'KEY_LEFTALT', AltRight: 'KEY_RIGHTALT',
  ControlLeft: 'KEY_LEFTCTRL', ControlRight: 'KEY_RIGHTCTRL',
  ShiftLeft: 'KEY_LEFTSHIFT', ShiftRight: 'KEY_RIGHTSHIFT',
  CapsLock: 'KEY_CAPSLOCK', Insert: 'KEY_INSERT',
  Delete: 'KEY_DELETE', Home: 'KEY_HOME', End: 'KEY_END',
  PageUp: 'KEY_PAGEUP', PageDown: 'KEY_PAGEDOWN',
  ScrollLock: 'KEY_SCROLLLOCK', Pause: 'KEY_PAUSE',
  PrintScreen: 'KEY_SYSRQ', NumLock: 'KEY_NUMLOCK',
  Backquote: 'KEY_GRAVE', Backslash: 'KEY_BACKSLASH',
};

const EVDEV_DISPLAY: Record<string, string> = {
  KEY_F1: 'F1', KEY_F2: 'F2', KEY_F3: 'F3', KEY_F4: 'F4',
  KEY_F5: 'F5', KEY_F6: 'F6', KEY_F7: 'F7', KEY_F8: 'F8',
  KEY_F9: 'F9', KEY_F10: 'F10', KEY_F11: 'F11', KEY_F12: 'F12',
  KEY_LEFTALT: 'Left Alt', KEY_RIGHTALT: 'Right Alt',
  KEY_LEFTCTRL: 'Left Ctrl', KEY_RIGHTCTRL: 'Right Ctrl',
  KEY_LEFTSHIFT: 'Left Shift', KEY_RIGHTSHIFT: 'Right Shift',
  KEY_CAPSLOCK: 'Caps Lock', KEY_INSERT: 'Insert',
  KEY_DELETE: 'Delete', KEY_HOME: 'Home', KEY_END: 'End',
  KEY_PAGEUP: 'Page Up', KEY_PAGEDOWN: 'Page Down',
  KEY_SCROLLLOCK: 'Scroll Lock', KEY_PAUSE: 'Pause',
  KEY_SYSRQ: 'Print Screen', KEY_NUMLOCK: 'Num Lock',
  KEY_GRAVE: 'Backtick', KEY_BACKSLASH: 'Backslash',
};

function displayKey(evdevKey: string | null | undefined, fallback: string): string {
  if (!evdevKey) return fallback;
  return EVDEV_DISPLAY[evdevKey] ?? evdevKey;
}

function KeyRecorder({ value, onChange }: { value: string | null; onChange: (key: string) => void }) {
  const [listening, setListening] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!listening) return;
    const handle = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const evdev = CODE_TO_EVDEV[e.code];
      if (evdev) {
        onChange(evdev);
        setListening(false);
      }
    };
    window.addEventListener('keydown', handle, true);
    return () => window.removeEventListener('keydown', handle, true);
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
      {listening ? 'Press a key…' : (value ? EVDEV_DISPLAY[value] ?? value : 'Not set')}
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
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings) {
      setPasteKey(settings.sttPasteKey ?? null);
      setVoiceKey(settings.sttVoiceKey ?? null);
    }
  }, [settings]);

  const save = useMutation({
    mutationFn: () => api.settings.updateShortcuts({
      sttPasteKey: pasteKey ?? undefined,
      sttVoiceKey: voiceKey ?? undefined,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    },
  });

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
              Default: <code>F1</code> · env: <code>STT_PASTE_KEY</code>
            </p>
          </div>
          <div>
            <p className="text-sm text-[var(--color-text)] mb-1.5">Voice shortcut</p>
            <p className="text-xs text-[var(--color-text-muted)] mb-2">Record → transcribe → AI chat → TTS reply</p>
            <KeyRecorder value={voiceKey} onChange={setVoiceKey} />
            <p className="text-xs text-[var(--color-text-muted)] mt-1">
              Default: <code>Right Alt</code> · env: <code>STT_VOICE_KEY</code>
            </p>
          </div>
        </div>
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

        {settings?.aiProvider === 'ollama' && ollamaModels && ollamaModels.length > 0 && (
          <div>
            <p className="text-sm font-medium text-[var(--color-text)] mb-2">LLM Model (Ollama)</p>
            <select
              value={settings.ollamaModel ?? ''}
              onChange={e => updateAI.mutate({ ollamaModel: e.target.value })}
              className="w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
            >
              {ollamaModels.map(m => (
                <option key={m.name} value={m.name}>{m.name} — {m.vramMb.toLocaleString()} MB</option>
              ))}
            </select>
          </div>
        )}

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

export function Settings() {
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
      <h1 className="text-2xl font-semibold text-[var(--color-text)] mb-6">Settings</h1>

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

      {settings?.networkMode && <NetworkSection />}

      <section>
        <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">About</h2>
        <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
          <p className="text-[var(--color-text)]">Lunaschal v0.1.0</p>
          <p className="text-sm text-[var(--color-text-muted)] mt-1">A privacy-first, self-hosted personal AI knowledge assistant.</p>
        </div>
      </section>
    </div>
  );
}
