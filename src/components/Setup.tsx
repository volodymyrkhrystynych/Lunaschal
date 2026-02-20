import { useState } from 'react';
import { trpc } from '../hooks/trpc';

export function Setup() {
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');

  const utils = trpc.useUtils();

  const setup = trpc.settings.setup.useMutation({
    onSuccess: () => {
      utils.settings.isSetupComplete.invalidate();
    },
    onError: (err) => {
      setError(err.message);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (password.length < 8) {
      setError('Password must be at least 8 characters');
      return;
    }

    if (password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }

    setup.mutate({ password });
  };

  return (
    <div className="min-h-screen bg-[var(--color-bg)] flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-[var(--color-text)] mb-2">Welcome to Lunaschal</h1>
          <p className="text-[var(--color-text-muted)]">
            Your personal AI knowledge assistant
          </p>
        </div>

        <form onSubmit={handleSubmit} className="bg-[var(--color-surface)] rounded-lg border border-white/10 p-6">
          <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">Create Your Password</h2>
          <p className="text-sm text-[var(--color-text-muted)] mb-6">
            Set a password to protect your personal data. You'll need this to access Lunaschal.
          </p>

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
                placeholder="At least 8 characters"
                className="w-full bg-transparent text-[var(--color-text)] border border-white/10 rounded-lg px-4 py-2 focus:outline-none focus:border-[var(--color-primary)]"
              />
            </div>

            <div>
              <label className="block text-sm text-[var(--color-text-muted)] mb-1">
                Confirm Password
              </label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Enter password again"
                className="w-full bg-transparent text-[var(--color-text)] border border-white/10 rounded-lg px-4 py-2 focus:outline-none focus:border-[var(--color-primary)]"
              />
            </div>

            <button
              type="submit"
              disabled={!password || !confirmPassword || setup.isPending}
              className="w-full py-3 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {setup.isPending ? 'Setting up...' : 'Get Started'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
