import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { api } from '../hooks/api';

interface Props {
  onSuccess: () => void;
}

export function Login({ onSuccess }: Props) {
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState('');

  const login = useMutation({
    mutationFn: () => api.auth.login(password, code),
    onSuccess: () => onSuccess(),
    onError: (err: Error) => setError(err.message),
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    login.mutate();
  };

  return (
    <div className="h-screen flex items-center justify-center bg-[var(--color-bg)]">
      <div className="w-full max-w-sm px-4">
        <h1 className="text-2xl font-semibold text-[var(--color-text)] mb-8 text-center">Lunaschal</h1>
        <form
          onSubmit={handleSubmit}
          className="bg-[var(--color-surface)] rounded-xl p-6 border border-white/10 space-y-4"
        >
          <div>
            <label className="block text-sm text-[var(--color-text-muted)] mb-1">Password</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              autoFocus
              className="w-full bg-[var(--color-bg)] border border-white/10 rounded px-3 py-2 text-[var(--color-text)] focus:outline-none focus:border-[var(--color-primary)]"
            />
          </div>
          <div>
            <label className="block text-sm text-[var(--color-text-muted)] mb-1">Display code</label>
            <input
              type="text"
              value={code}
              onChange={e => setCode(e.target.value)}
              placeholder="6-digit code shown in Settings on the server"
              className="w-full bg-[var(--color-bg)] border border-white/10 rounded px-3 py-2 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
            />
          </div>
          {error && <p className="text-sm text-red-400">{error}</p>}
          <button
            type="submit"
            disabled={!password || !code || login.isPending}
            className="w-full py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50 transition-colors"
          >
            {login.isPending ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  );
}
