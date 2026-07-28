import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';

export function NetworkSection() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });

  const regenerate = useMutation({
    mutationFn: api.settings.regenerateCode,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const toggleSleep = useMutation({
    mutationFn: (enabled: boolean) =>
      api.settings.updateAI({ preventSleep: enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const preventSleep = settings?.preventSleep ?? false;

  const logout = useMutation({
    mutationFn: api.auth.logout,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['auth', 'status'] }),
  });

  const origin = window.location.origin;

  return (
    <section className="mb-8">
      <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">
        Network Access
      </h2>
      <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10 space-y-4">
        <div>
          <p className="text-sm text-[var(--color-text-muted)] mb-1">
            Connect from your laptop at:
          </p>
          <code className="text-sm text-[var(--color-primary)]">{origin}</code>
        </div>
        <div>
          <p className="text-sm text-[var(--color-text-muted)] mb-2">
            Display code (second factor):
          </p>
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
            Laptop sign-in requires this code plus{' '}
            <code>LUNASCHAL_PASSWORD</code>. Regenerate after each remote
            session.
          </p>
        </div>
        <label className="flex items-center gap-3 cursor-pointer select-none pt-2 border-t border-white/10">
          <div
            onClick={() => toggleSleep.mutate(!preventSleep)}
            className={`relative w-9 h-5 rounded-full transition-colors ${preventSleep ? 'bg-[var(--color-primary)]' : 'bg-white/20'}`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${preventSleep ? 'translate-x-4' : 'translate-x-0'}`}
            />
          </div>
          <div>
            <span className="text-sm text-[var(--color-text)]">
              Prevent sleep
            </span>
            <p className="text-xs text-[var(--color-text-muted)]">
              Keep the server awake while running
            </p>
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
