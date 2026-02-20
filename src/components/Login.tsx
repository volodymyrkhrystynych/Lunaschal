import { useState } from 'react';
import { trpc } from '../hooks/trpc';

interface LoginProps {
  onSuccess: () => void;
}

export function Login({ onSuccess }: LoginProps) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');

  const login = trpc.settings.login.useMutation({
    onSuccess: () => {
      onSuccess();
    },
    onError: (err) => {
      setError(err.message);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!password) {
      setError('Please enter your password');
      return;
    }

    login.mutate({ password });
  };

  return (
    <div className="min-h-screen bg-[var(--color-bg)] flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-[var(--color-text)] mb-2">Lunaschal</h1>
          <p className="text-[var(--color-text-muted)]">
            Enter your password to continue
          </p>
        </div>

        <form onSubmit={handleSubmit} className="bg-[var(--color-surface)] rounded-lg border border-white/10 p-6">
          {error && (
            <div className="mb-4 p-3 bg-red-900/30 border border-red-600/50 rounded-lg text-red-200 text-sm">
              {error}
            </div>
          )}

          <div className="space-y-4">
            <div>
              <label className="block text-sm text-[var(--color-text-muted)] mb-1">
                Password
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter your password"
                autoFocus
                className="w-full bg-transparent text-[var(--color-text)] border border-white/10 rounded-lg px-4 py-2 focus:outline-none focus:border-[var(--color-primary)]"
              />
            </div>

            <button
              type="submit"
              disabled={!password || login.isPending}
              className="w-full py-3 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {login.isPending ? 'Logging in...' : 'Login'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
