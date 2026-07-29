import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';

export function NudgeSection() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });
  const [intervalInput, setIntervalInput] = useState('45');

  useEffect(() => {
    if (settings) {
      setIntervalInput(String(settings.nudgeIntervalMinutes ?? 45));
    }
  }, [settings]);

  const toggleEnabled = useMutation({
    mutationFn: (enabled: boolean) =>
      api.settings.updateAI({ nudgeEnabled: enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const saveInterval = useMutation({
    mutationFn: (minutes: number) =>
      api.settings.updateAI({ nudgeIntervalMinutes: minutes }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const nudgeEnabled = settings?.nudgeEnabled ?? true;

  const commitInterval = () => {
    const minutes = Math.max(1, parseInt(intervalInput, 10) || 45);
    setIntervalInput(String(minutes));
    saveInterval.mutate(minutes);
  };

  return (
    <section className="mb-8">
      <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">
        Task Nudges
      </h2>
      <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10 space-y-4">
        <p className="text-sm text-[var(--color-text-muted)]">
          Periodic voice check-ins on your top pending task. Restart the STT
          listener for changes to take effect.
        </p>
        <label className="flex items-center gap-3 cursor-pointer select-none">
          <div
            onClick={() => toggleEnabled.mutate(!nudgeEnabled)}
            className={`relative w-9 h-5 rounded-full transition-colors ${nudgeEnabled ? 'bg-[var(--color-primary)]' : 'bg-white/20'}`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${nudgeEnabled ? 'translate-x-4' : 'translate-x-0'}`}
            />
          </div>
          <span className="text-sm text-[var(--color-text)]">
            Enable task nudges
          </span>
        </label>
        {nudgeEnabled && (
          <div>
            <label className="text-sm text-[var(--color-text-muted)]">
              Nudge interval (minutes)
            </label>
            <input
              type="number"
              min={1}
              value={intervalInput}
              onChange={e => setIntervalInput(e.target.value)}
              onBlur={commitInterval}
              className="mt-1 w-32 bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
            />
            <p className="text-xs text-[var(--color-text-muted)] mt-1">
              env: <code>NUDGE_INTERVAL</code> (seconds, used only if this
              setting is unset)
            </p>
          </div>
        )}
      </div>
    </section>
  );
}
