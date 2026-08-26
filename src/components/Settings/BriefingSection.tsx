import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';

export function BriefingSection() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });
  const { data: llamaModels } = useQuery({
    queryKey: ['settings', 'llama-models'],
    queryFn: api.settings.llamaModels,
    enabled: !!settings?.llamaUrl,
  });
  const [hourInput, setHourInput] = useState('5');
  const [goalsInput, setGoalsInput] = useState('');
  const [maxTokensInput, setMaxTokensInput] = useState('16384');
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    if (settings) {
      setHourInput(String(settings.briefingHour ?? 5));
      setGoalsInput(settings.briefingGoals ?? '');
      setMaxTokensInput(String(settings.briefingMaxTokens ?? 16384));
    }
  }, [settings]);

  const toggleEnabled = useMutation({
    mutationFn: (enabled: boolean) =>
      api.settings.updateAI({ briefingEnabled: enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const saveModel = useMutation({
    mutationFn: (model: string) =>
      api.settings.updateAI({ briefingModel: model || null }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const saveHour = useMutation({
    mutationFn: (hour: number) => api.settings.updateAI({ briefingHour: hour }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const saveGoals = useMutation({
    mutationFn: (goals: string) =>
      api.settings.updateAI({ briefingGoals: goals }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const saveThinking = useMutation({
    mutationFn: (thinking: boolean) =>
      api.settings.updateAI({ briefingThinking: thinking }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const saveMaxTokens = useMutation({
    mutationFn: (maxTokens: number) =>
      api.settings.updateAI({ briefingMaxTokens: maxTokens }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const generateNow = useMutation({
    mutationFn: api.chat.runBriefing,
    onSuccess: result => {
      queryClient.invalidateQueries({ queryKey: ['chat', 'today'] });
      queryClient.invalidateQueries({ queryKey: ['chatTodos'] });
      setStatus(
        `Briefing ready — ${result.todosAdded} to-do${result.todosAdded === 1 ? '' : 's'} added to today's chat bar.`
      );
      setTimeout(() => setStatus(null), 5000);
    },
    onError: (error: Error) => {
      setStatus(error.message);
      setTimeout(() => setStatus(null), 5000);
    },
  });

  const briefingEnabled = settings?.briefingEnabled ?? true;

  const briefingThinking = settings?.briefingThinking ?? false;

  const commitHour = () => {
    const hour = Math.min(23, Math.max(4, parseInt(hourInput, 10) || 5));
    setHourInput(String(hour));
    saveHour.mutate(hour);
  };

  const commitMaxTokens = () => {
    const tokens = Math.min(
      65536,
      Math.max(256, parseInt(maxTokensInput, 10) || 16384)
    );
    setMaxTokensInput(String(tokens));
    if (tokens !== (settings?.briefingMaxTokens ?? 16384)) {
      saveMaxTokens.mutate(tokens);
    }
  };

  return (
    <>
      <p className="text-sm text-[var(--color-text-muted)]">
        While the machine is on overnight, an agent reads your recent journal,
        tasks, calendar and reviews, then leaves a morning briefing as the first
        message of the day's chat and adds any suggested to-dos.
      </p>
      <label className="flex items-center gap-3 cursor-pointer select-none">
        <div
          onClick={() => toggleEnabled.mutate(!briefingEnabled)}
          className={`relative w-9 h-5 rounded-full transition-colors ${briefingEnabled ? 'bg-[var(--color-primary)]' : 'bg-white/20'}`}
        >
          <span
            className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${briefingEnabled ? 'translate-x-4' : 'translate-x-0'}`}
          />
        </div>
        <span className="text-sm text-[var(--color-text)]">
          Enable overnight briefing
        </span>
      </label>
      {briefingEnabled && (
        <>
          <div>
            <label className="text-sm text-[var(--color-text-muted)]">
              Briefing hour (local, 4–23)
            </label>
            <input
              type="number"
              min={4}
              max={23}
              value={hourInput}
              onChange={e => setHourInput(e.target.value)}
              onBlur={commitHour}
              className="mt-1 w-32 bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
            />
            <p className="text-xs text-[var(--color-text-muted)] mt-1">
              Fires once in the hour window after this time; must be ≥ 4am so it
              lands in the new day's chat.
            </p>
          </div>
          <div>
            <label className="text-sm text-[var(--color-text-muted)]">
              Briefing model
            </label>
            <select
              value={settings?.briefingModel ?? ''}
              onChange={e => saveModel.mutate(e.target.value)}
              className="mt-1 w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
            >
              <option value="">Same as chat model (default)</option>
              {llamaModels && llamaModels.length > 0 && (
                <optgroup label="Router presets">
                  {llamaModels.map(m => (
                    <option key={m.name} value={m.name}>
                      {m.name} — {m.status}
                    </option>
                  ))}
                </optgroup>
              )}
            </select>
            <p className="text-xs text-[var(--color-text-muted)] mt-1">
              Runs overnight, so a slower preset is fine here. There is only one
              chat preset now — <code>qwen36</code> — so leave this on the
              default unless you add another to <code>llama/presets.ini</code>.
            </p>
          </div>
          <div>
            <label className="text-sm text-[var(--color-text-muted)]">
              Thinking
            </label>
            <label className="mt-1 flex items-center gap-3 cursor-pointer">
              <div
                onClick={() => saveThinking.mutate(!briefingThinking)}
                className={`relative w-9 h-5 rounded-full transition-colors shrink-0 ${briefingThinking ? 'bg-[var(--color-primary)]' : 'bg-white/20'}`}
              >
                <span
                  className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${briefingThinking ? 'translate-x-4' : 'translate-x-0'}`}
                />
              </div>
              <span className="text-sm text-[var(--color-text)]">
                Think before writing
              </span>
            </label>
            <p className="text-xs text-[var(--color-text-muted)] mt-1">
              Worth turning on here even though chat leaves it off: the briefing
              runs while you're asleep, so the extra tokens cost you nothing.
              Give it plenty of output tokens below if you do — thinking and the
              answer share the same budget.
            </p>
          </div>
          <div>
            <label className="text-sm text-[var(--color-text-muted)]">
              Output token limit (256–65536)
            </label>
            <input
              type="number"
              min={256}
              max={65536}
              step={256}
              value={maxTokensInput}
              onChange={e => setMaxTokensInput(e.target.value)}
              onBlur={commitMaxTokens}
              className="mt-1 w-32 bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
            />
            <p className="text-xs text-[var(--color-text-muted)] mt-1">
              Ceiling on the briefing's length. Generous by default since it
              runs overnight; raise it if long briefings get cut off.
            </p>
          </div>
          <p className="text-xs text-[var(--color-text-muted)]">
            The context window isn't an app setting: llama-server allocates the
            KV cache when it loads the model, so it's fixed per preset in{' '}
            <code>llama/presets.ini</code>. Raise <code>ctx-size</code> there
            and restart llama-server if a briefing ever needs a bigger one.
          </p>
          <div>
            <label className="text-sm text-[var(--color-text-muted)]">
              Goals &amp; current focus
            </label>
            <textarea
              value={goalsInput}
              onChange={e => setGoalsInput(e.target.value)}
              onBlur={() => {
                if (goalsInput !== (settings?.briefingGoals ?? '')) {
                  saveGoals.mutate(goalsInput);
                }
              }}
              rows={5}
              placeholder="What you're working towards, current projects, what's on your mind…"
              className="mt-1 w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm resize-y focus:outline-none focus:border-[var(--color-primary)]"
            />
            <p className="text-xs text-[var(--color-text-muted)] mt-1">
              Standing context the secretary reads every night. Saved when you
              click away.
            </p>
          </div>
        </>
      )}
      <div className="flex items-center gap-3">
        <button
          onClick={() => generateNow.mutate()}
          disabled={generateNow.isPending}
          className="px-3 py-1.5 text-sm rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40 hover:bg-[var(--color-primary)]/30 disabled:opacity-50"
        >
          {generateNow.isPending ? 'Generating…' : 'Generate now'}
        </button>
        {status && (
          <span className="text-xs text-[var(--color-text-muted)]">
            {status}
          </span>
        )}
      </div>
    </>
  );
}
