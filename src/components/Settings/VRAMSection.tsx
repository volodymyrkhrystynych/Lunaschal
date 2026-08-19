import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { vramColors } from '../../lib/vram';

const VRAM_TOTAL_MB = 8192;
const KOKORO_VRAM_MB = 80;

const WHISPER_VRAM_TABLE: Record<string, number> = {
  tiny: 1024,
  base: 1024,
  small: 2048,
  medium: 5120,
  turbo: 6144,
  'large-v3': 10240,
};

export function VRAMSection() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });
  const { data: whisperModels } = useQuery({
    queryKey: ['stt', 'whisper-models'],
    queryFn: api.stt.whisperModels,
  });
  const { data: gpuVram } = useQuery({
    queryKey: ['settings', 'gpu-vram'],
    queryFn: api.settings.gpuVram,
    // The LLM's share is a live reading now, and it changes when a model loads
    // or unloads — a once-per-mount value would go stale within a minute.
    refetchInterval: 5000,
  });

  const [saved, setSaved] = useState(false);
  const [hfToken, setHfToken] = useState('');
  const [llmMaxTokensInput, setLlmMaxTokensInput] = useState('4096');
  const [chatTimeoutInput, setChatTimeoutInput] = useState('900');

  useEffect(() => {
    if (settings) {
      setLlmMaxTokensInput(String(settings.llmMaxTokens ?? 4096));
      setChatTimeoutInput(String(settings.chatTimeoutSeconds ?? 900));
    }
  }, [settings]);

  const updateAI = useMutation({
    mutationFn: api.settings.updateAI,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    },
  });

  const commitLlmMaxTokens = () => {
    const tokens = Math.min(
      65536,
      Math.max(256, parseInt(llmMaxTokensInput, 10) || 4096)
    );
    setLlmMaxTokensInput(String(tokens));
    if (tokens !== (settings?.llmMaxTokens ?? 4096)) {
      updateAI.mutate({ llmMaxTokens: tokens });
    }
  };

  const commitChatTimeout = () => {
    const seconds = Math.min(
      7200,
      Math.max(30, parseInt(chatTimeoutInput, 10) || 900)
    );
    setChatTimeoutInput(String(seconds));
    if (seconds !== (settings?.chatTimeoutSeconds ?? 900)) {
      updateAI.mutate({ chatTimeoutSeconds: seconds });
    }
  };

  const reloadStt = useMutation({
    mutationFn: api.stt.reload,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['stt', 'health'] }),
  });

  // Defaults match backend/routes/stt.py: Parakeet on CPU, because llama-server
  // holds most of the card for as long as it runs.
  const activeSttBackend = settings?.sttBackend ?? 'parakeet';
  const activeTtsBackend = settings?.ttsBackend ?? 'local';
  const activeWhisperModel = settings?.whisperModel ?? 'turbo';
  const activeSttDevice = settings?.sttDevice ?? 'cpu';

  const whisperVram =
    activeSttBackend === 'local' && activeSttDevice !== 'cpu'
      ? (WHISPER_VRAM_TABLE[activeWhisperModel] ?? 6144)
      : 0;
  const kokoroVram = activeTtsBackend === 'local' ? KOKORO_VRAM_MB : 0;
  // Measured, not estimated. With Gemma 4's expert tensors split between GPU and
  // system RAM, the model's GPU footprint is set by n-cpu-moe, the KV cache and
  // the batch size — the file size says nothing useful about it.
  const llmVram = gpuVram?.llmMb ?? 0;
  // baseVram is whatever else was already using the GPU (browser, compositor,
  // etc.) measured once when the server started, excluding llama-server — not
  // live, so it won't reflect changes since then, but it's enough to warn
  // against over-budgeting.
  const baseVram = gpuVram?.available ? (gpuVram.baseMb ?? 0) : 0;
  const effectiveTotalMb =
    gpuVram?.available && gpuVram.totalMb ? gpuVram.totalMb : VRAM_TOTAL_MB;
  // Prefer the card's live total when we have it: it catches everything,
  // including whatever the browser grabbed since startup. Fall back to summing
  // what we know about.
  const totalVram =
    gpuVram?.usedMb ?? baseVram + whisperVram + kokoroVram + llmVram;
  const vramPct = Math.min(100, (totalVram / effectiveTotalMb) * 100);
  const { bar: barColor, text: numColor } = vramColors(vramPct);

  const setSttBackend = (backend: string) => {
    updateAI.mutate({ sttBackend: backend });
    reloadStt.mutate();
  };

  const setWhisperModel = (model: string) => {
    updateAI.mutate({ whisperModel: model });
    reloadStt.mutate();
  };

  const setSttDevice = (device: string) => {
    updateAI.mutate({ sttDevice: device });
    reloadStt.mutate();
  };

  return (
    <div className="space-y-5">
      <div>
        <div className="flex justify-between text-sm mb-1.5">
          <span className="text-[var(--color-text-muted)]">
            {(effectiveTotalMb / 1024).toFixed(1)} GB VRAM budget
          </span>
          <span className={`font-medium ${numColor}`}>
            {totalVram.toLocaleString()} / {effectiveTotalMb.toLocaleString()}{' '}
            MB
          </span>
        </div>
        <div className="h-2.5 bg-white/10 rounded-full overflow-hidden">
          <div
            className={`h-full ${barColor} rounded-full transition-all duration-300`}
            style={{ width: `${vramPct}%` }}
          />
        </div>
        <div className="flex flex-wrap gap-x-4 mt-1.5 text-xs text-[var(--color-text-muted)]">
          {gpuVram?.available && (
            <span>Base (other apps): {baseVram.toLocaleString()} MB</span>
          )}
          <span>
            STT:{' '}
            {activeSttBackend === 'local'
              ? `${whisperVram} MB`
              : activeSttBackend === 'parakeet'
                ? '0 MB (CPU)'
                : '0 MB (cloud)'}
          </span>
          <span>
            TTS:{' '}
            {activeTtsBackend === 'local' ? `${kokoroVram} MB` : '0 MB (cloud)'}
          </span>
          <span>
            LLM:{' '}
            {llmVram > 0
              ? `${llmVram.toLocaleString()} MB (measured)`
              : 'not loaded'}
          </span>
        </div>
        {gpuVram?.available === false && (
          <p className="text-xs text-[var(--color-text-muted)] mt-1.5">
            GPU VRAM detection unavailable (no nvidia-smi) — budget assumes the
            whole card is free.
          </p>
        )}
        {gpuVram?.available && llmVram === 0 && (
          <p className="text-xs text-[var(--color-text-muted)] mt-1.5">
            llama-server isn't holding any VRAM — either it isn't running or no
            model is loaded yet. AI features will fail until it is.
          </p>
        )}
      </div>

      <div>
        <p className="text-sm font-medium text-[var(--color-text)] mb-2">
          Speech-to-Text (STT)
        </p>
        <div className="flex gap-2 mb-2">
          {(['local', 'parakeet', 'openai'] as const).map(b => (
            <button
              key={b}
              onClick={() => setSttBackend(b)}
              className={`px-3 py-1.5 rounded text-sm border transition-colors ${
                activeSttBackend === b
                  ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/15 text-[var(--color-primary)]'
                  : 'border-white/20 bg-white/5 hover:bg-white/10 text-[var(--color-text-muted)]'
              }`}
            >
              {b === 'local'
                ? 'Local (Whisper)'
                : b === 'parakeet'
                  ? 'Parakeet (CPU)'
                  : 'OpenAI API'}
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
              <option key={m.name} value={m.name}>
                {m.name} — {m.vramMb} MB
              </option>
            ))}
          </select>
        )}
        {activeSttBackend === 'local' && (
          <div className="flex gap-2 mt-2">
            {(['cuda', 'cpu'] as const).map(d => (
              <button
                key={d}
                onClick={() => setSttDevice(d)}
                className={`px-3 py-1.5 rounded text-sm border transition-colors ${
                  activeSttDevice === d
                    ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/15 text-[var(--color-primary)]'
                    : 'border-white/20 bg-white/5 hover:bg-white/10 text-[var(--color-text-muted)]'
                }`}
              >
                {d === 'cuda' ? 'GPU' : 'CPU'}
              </button>
            ))}
          </div>
        )}
        {activeSttBackend === 'local' &&
          activeSttDevice === 'cuda' &&
          llmVram > 0 && (
            <p className="text-xs text-yellow-400 mt-2">
              llama-server is holding {llmVram.toLocaleString()} MB and won't
              release it. Whisper needs {whisperVram.toLocaleString()} MB on top
              — with {effectiveTotalMb.toLocaleString()} MB total, whichever
              loads second will likely fail. Use Parakeet (CPU), switch Whisper
              to CPU, or unload the model first.
            </p>
          )}
      </div>

      <div>
        <p className="text-sm font-medium text-[var(--color-text)] mb-2">
          Text-to-Speech (TTS)
        </p>
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

      <div>
        <p className="text-sm font-medium text-[var(--color-text)] mb-2">
          Meeting recording
        </p>
        <label className="flex items-center gap-3 cursor-pointer">
          <div
            onClick={() =>
              updateAI.mutate({
                meetingEchoCancel: !(settings?.meetingEchoCancel ?? false),
              })
            }
            className={`relative w-9 h-5 rounded-full transition-colors ${settings?.meetingEchoCancel ? 'bg-[var(--color-primary)]' : 'bg-white/20'}`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${settings?.meetingEchoCancel ? 'translate-x-4' : 'translate-x-0'}`}
            />
          </div>
          <div>
            <span className="text-sm text-[var(--color-text)]">
              Echo cancellation
            </span>
            <p className="text-xs text-[var(--color-text-muted)]">
              Keeps the other participants out of your mic track when using
              speakers instead of headphones. May soften your voice while others
              are talking. Falls back to the raw mic automatically if
              unavailable.
            </p>
          </div>
        </label>
      </div>

      <div>
        <p className="text-sm font-medium text-[var(--color-text)] mb-2">
          Speaker diarization (meetings)
        </p>
        <p className="text-xs text-[var(--color-text-muted)] mb-2">
          HuggingFace token for pyannote speaker diarization — used to tell
          meeting participants apart. Without it, remote speakers are all
          labeled "Others".
        </p>
        <div className="flex gap-2">
          <input
            type="password"
            value={hfToken}
            onChange={e => setHfToken(e.target.value)}
            placeholder={settings?.hasHfToken ? '••••••••••••••••' : 'hf_...'}
            className="flex-1 bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
          />
          <button
            onClick={() => {
              updateAI.mutate({ hfToken });
              setHfToken('');
            }}
            disabled={!hfToken.trim()}
            className="px-3 py-1.5 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10 text-[var(--color-text)] disabled:opacity-50"
          >
            Save
          </button>
        </div>
      </div>

      <div>
        <p className="text-sm font-medium text-[var(--color-text)] mb-2">
          Thinking
        </p>
        <label className="flex items-center gap-3 cursor-pointer">
          <div
            onClick={() =>
              updateAI.mutate({
                llmThinking: !(settings?.llmThinking ?? false),
              })
            }
            className={`relative w-9 h-5 rounded-full transition-colors ${settings?.llmThinking ? 'bg-[var(--color-primary)]' : 'bg-white/20'}`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${settings?.llmThinking ? 'translate-x-4' : 'translate-x-0'}`}
            />
          </div>
          <div>
            <span className="text-sm text-[var(--color-text)]">
              Think before replying
            </span>
            <p className="text-xs text-[var(--color-text-muted)]">
              Gemma 4 has a single thinking channel — on or off, no graded
              levels, and it isn't bounded. Measured here, the same trivial
              prompt takes <strong>25s</strong> to the first word with thinking
              on versus <strong>1.3s</strong> with it off, because thinking
              tokens aren't streamed to you. Leave it off for chat; the
              overnight briefing has its own toggle, where latency is free.
            </p>
          </div>
        </label>
      </div>
      <div>
        <p className="text-sm font-medium text-[var(--color-text)] mb-2">
          Output token limit (256–65536)
        </p>
        <input
          type="number"
          min={256}
          max={65536}
          step={256}
          value={llmMaxTokensInput}
          onChange={e => setLlmMaxTokensInput(e.target.value)}
          onBlur={commitLlmMaxTokens}
          className="w-32 bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
        />
        <p className="text-xs text-[var(--color-text-muted)] mt-1">
          Ceiling on each reply's length. Also caps runaway repetition loops.
        </p>
      </div>
      <div>
        <p className="text-sm font-medium text-[var(--color-text)] mb-2">
          Reply time limit
        </p>
        <label className="flex items-center gap-3 cursor-pointer mb-2">
          <div
            onClick={() =>
              updateAI.mutate({
                chatTimeoutEnabled: !(settings?.chatTimeoutEnabled ?? true),
              })
            }
            className={`relative w-9 h-5 rounded-full transition-colors ${settings?.chatTimeoutEnabled ? 'bg-[var(--color-primary)]' : 'bg-white/20'}`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${settings?.chatTimeoutEnabled ? 'translate-x-4' : 'translate-x-0'}`}
            />
          </div>
          <span className="text-sm text-[var(--color-text)]">
            Give up on a reply that runs too long
          </span>
        </label>
        {settings?.chatTimeoutEnabled && (
          <input
            type="number"
            min={30}
            max={7200}
            step={30}
            value={chatTimeoutInput}
            onChange={e => setChatTimeoutInput(e.target.value)}
            onBlur={commitChatTimeout}
            className="w-32 bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
          />
        )}
        <p className="text-xs text-[var(--color-text-muted)] mt-1">
          Seconds (30–7200) for a whole reply, research included. Whatever
          streamed is kept and labelled — it is a real partial answer, not a
          failure. This is also the outer bound the research limits in the Ideas
          section are clamped to, so neither can outlive it.
        </p>
      </div>
      <div>
        <p className="text-sm font-medium text-[var(--color-text)] mb-2">
          Context window
        </p>
        <p className="text-xs text-[var(--color-text-muted)]">
          Not an app setting any more. llama-server allocates the KV cache once
          when it loads the model, so the window is fixed per preset — set{' '}
          <code className="text-[var(--color-text)]">ctx-size</code> in{' '}
          <code className="text-[var(--color-text)]">llama/presets.ini</code>{' '}
          and restart it. The current value is shown under llama.cpp
          Configuration above.
        </p>
      </div>

      {saved && <p className="text-xs text-green-400">Saved</p>}
    </div>
  );
}
