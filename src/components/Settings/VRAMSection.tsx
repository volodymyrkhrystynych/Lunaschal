import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type AppSettings } from '../../hooks/api';
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

// Small, GPU-friendly models that leave headroom for STT/TTS in an 8GB
// budget — shown as pull suggestions for whichever of these aren't
// installed yet. vramMb figures are measured, not estimated.
const RECOMMENDED_MODELS: { name: string; vramMb: number; note: string }[] = [
  { name: 'llama3.2:latest', vramMb: 2311, note: 'small, fast, well-rounded' },
  { name: 'phi4-mini:3.8b', vramMb: 2852, note: 'strong for its size' },
  {
    name: 'phi4-mini-reasoning:latest',
    vramMb: 3608,
    note: 'reasoning-focused',
  },
  { name: 'qwen3.5:2b', vramMb: 3137, note: 'compact, multilingual' },
];

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
  const { data: ollamaModels } = useQuery({
    queryKey: ['settings', 'ollama-models'],
    queryFn: api.settings.ollamaModels,
    enabled: !!settings?.ollamaUrl,
  });
  const { data: gpuVram } = useQuery({
    queryKey: ['settings', 'gpu-vram'],
    queryFn: api.settings.gpuVram,
  });

  const [saved, setSaved] = useState(false);
  const [hfToken, setHfToken] = useState('');
  const [llmMaxTokensInput, setLlmMaxTokensInput] = useState('4096');
  const [llmNumCtxInput, setLlmNumCtxInput] = useState('4096');

  useEffect(() => {
    if (settings) {
      setLlmMaxTokensInput(String(settings.llmMaxTokens ?? 4096));
      setLlmNumCtxInput(String(settings.llmNumCtx ?? 4096));
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

  const commitLlmNumCtx = () => {
    const ctx = Math.min(
      131072,
      Math.max(512, parseInt(llmNumCtxInput, 10) || 4096)
    );
    setLlmNumCtxInput(String(ctx));
    if (ctx !== (settings?.llmNumCtx ?? 4096)) {
      updateAI.mutate({ llmNumCtx: ctx });
    }
  };

  const reloadStt = useMutation({
    mutationFn: api.stt.reload,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['stt', 'health'] }),
  });

  const activeSttBackend = settings?.sttBackend ?? 'local';
  const activeTtsBackend = settings?.ttsBackend ?? 'local';
  const activeWhisperModel = settings?.whisperModel ?? 'turbo';
  const activeSttDevice = settings?.sttDevice ?? 'cuda';

  const whisperVram =
    activeSttBackend === 'local' && activeSttDevice !== 'cpu'
      ? (WHISPER_VRAM_TABLE[activeWhisperModel] ?? 6144)
      : 0;
  const kokoroVram = activeTtsBackend === 'local' ? KOKORO_VRAM_MB : 0;
  const ollamaVram = settings?.ollamaModel
    ? (ollamaModels?.find(m => m.name === settings.ollamaModel)?.vramMb ?? 0)
    : 0;
  // baseVram is whatever else was already using the GPU (browser, compositor,
  // etc.) measured once when the server started — not live, so it won't
  // reflect changes since then, but it's enough to warn against over-budgeting.
  const baseVram = gpuVram?.available ? (gpuVram.baseMb ?? 0) : 0;
  const effectiveTotalMb =
    gpuVram?.available && gpuVram.totalMb ? gpuVram.totalMb : VRAM_TOTAL_MB;
  const totalVram = baseVram + whisperVram + kokoroVram + ollamaVram;
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
    <section className="mb-8">
      <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">
        Model & VRAM
      </h2>
      <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10 space-y-5">
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
              {activeTtsBackend === 'local'
                ? `${kokoroVram} MB`
                : '0 MB (cloud)'}
            </span>
            <span>
              LLM:{' '}
              {ollamaVram > 0
                ? `~${ollamaVram.toLocaleString()} MB`
                : 'unknown'}
            </span>
          </div>
          {gpuVram?.available === false && (
            <p className="text-xs text-[var(--color-text-muted)] mt-1.5">
              GPU VRAM detection unavailable (no nvidia-smi) — budget assumes
              the whole card is free.
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
                speakers instead of headphones. May soften your voice while
                others are talking. Falls back to the raw mic automatically if
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

        {ollamaModels &&
          ollamaModels.length > 0 &&
          (() => {
            const installedNames = new Set(ollamaModels.map(m => m.name));
            const notInstalled = RECOMMENDED_MODELS.filter(
              r => !installedNames.has(r.name)
            );
            return (
              <div>
                <p className="text-sm font-medium text-[var(--color-text)] mb-2">
                  LLM Model (Ollama)
                </p>
                <select
                  value={settings?.ollamaModel ?? ''}
                  onChange={e =>
                    updateAI.mutate({ ollamaModel: e.target.value })
                  }
                  className="w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
                >
                  <optgroup label="Installed">
                    {ollamaModels.map(m => (
                      <option key={m.name} value={m.name}>
                        {m.name} — {m.vramMb.toLocaleString()} MB
                      </option>
                    ))}
                  </optgroup>
                  {notInstalled.length > 0 && (
                    <optgroup label="Recommended (not installed)">
                      {notInstalled.map(m => (
                        <option key={m.name} value={m.name}>
                          {m.name} — ~{m.vramMb.toLocaleString()} MB · {m.note}
                        </option>
                      ))}
                    </optgroup>
                  )}
                </select>
              </div>
            );
          })()}

        <div>
          <p className="text-sm font-medium text-[var(--color-text)] mb-2">
            Thinking effort
          </p>
          <select
            value={settings?.llmReasoningEffort ?? 'none'}
            onChange={e =>
              updateAI.mutate({
                llmReasoningEffort: e.target
                  .value as AppSettings['llmReasoningEffort'],
              })
            }
            className="w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
          >
            <option value="none">None — don't think (default)</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
            <option value="max">Max</option>
          </select>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">
            How hard the model reasons before replying. Applies to the model
            above regardless of whether it's a thinking model — non-reasoning
            models simply ignore it. Keep it <em>None</em> unless this model
            supports thinking, and raise the token limit below if you turn it
            up.
          </p>
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
            Context window (512–131072)
          </p>
          <input
            type="number"
            min={512}
            max={131072}
            step={512}
            value={llmNumCtxInput}
            onChange={e => setLlmNumCtxInput(e.target.value)}
            onBlur={commitLlmNumCtx}
            className="w-32 bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
          />
          <p className="text-xs text-[var(--color-text-muted)] mt-1">
            How many tokens (prompt + thinking + reply) the model can hold at
            once. Ollama's default is 4096; raise it if you turn thinking on, so
            reasoning doesn't crowd out the answer. Bigger uses more VRAM. This
            one window applies to every AI call — chat, the overnight briefing,
            and background helpers — because Ollama reloads the model whenever
            the context size changes between requests.
          </p>
        </div>

        {saved && <p className="text-xs text-green-400">Saved</p>}
      </div>
    </section>
  );
}
