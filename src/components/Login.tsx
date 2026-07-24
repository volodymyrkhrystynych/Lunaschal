import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { api } from '../hooks/api';
import {
  loadCachedCode,
  saveCachedCode,
  cacheExpiresInDays,
} from '../lib/networkCode';

interface Props {
  onSuccess: () => void;
}

export function Login({ onSuccess }: Props) {
  const [password, setPassword] = useState('');
  // The display code is stable on the server, so once a device has entered it we
  // remember it locally for a week (see lib/networkCode) and only ask for the
  // password. `codeRemembered` hides the code field while a fresh cached code is
  // pre-filling `code`.
  const cachedCode = loadCachedCode();
  const [code, setCode] = useState(cachedCode ?? '');
  const [codeRemembered, setCodeRemembered] = useState(cachedCode !== null);
  const [error, setError] = useState('');
  const daysLeft = codeRemembered ? cacheExpiresInDays() : null;

  const login = useMutation({
    mutationFn: () => api.auth.login(password, code),
    onSuccess: () => {
      saveCachedCode(code);
      onSuccess();
    },
    onError: (err: Error) => {
      setError(err.message);
      // Can't tell whether the password or the code was wrong (the backend
      // returns a combined message), so reveal the code field — it may be a
      // stale code (server regenerated) the user now needs to re-enter. Keep the
      // pre-filled value in case it was just a password typo; don't clear the
      // cache.
      setCodeRemembered(false);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    login.mutate();
  };

  return (
    <div className="h-dvh flex items-center justify-center bg-[var(--color-bg)]">
      <div className="w-full max-w-sm px-4">
        <h1 className="text-2xl font-semibold text-[var(--color-text)] mb-8 text-center">
          Lunaschal
        </h1>
        <form
          onSubmit={handleSubmit}
          className="bg-[var(--color-surface)] rounded-xl p-6 border border-white/10 space-y-4"
        >
          <div>
            <label className="block text-sm text-[var(--color-text-muted)] mb-1">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              autoFocus
              className="w-full bg-[var(--color-bg)] border border-white/10 rounded px-3 py-2 text-[var(--color-text)] focus:outline-none focus:border-[var(--color-primary)]"
            />
          </div>
          {codeRemembered ? (
            <p className="text-xs text-[var(--color-text-muted)]">
              Display code remembered on this device
              {daysLeft !== null &&
                ` (expires in ${daysLeft} day${daysLeft === 1 ? '' : 's'})`}
              .{' '}
              <button
                type="button"
                onClick={() => setCodeRemembered(false)}
                className="underline hover:text-[var(--color-text)]"
              >
                Use a different code
              </button>
            </p>
          ) : (
            <div>
              <label className="block text-sm text-[var(--color-text-muted)] mb-1">
                Display code
              </label>
              <input
                type="text"
                value={code}
                onChange={e => setCode(e.target.value)}
                placeholder="6-digit code shown in Settings on the server"
                className="w-full bg-[var(--color-bg)] border border-white/10 rounded px-3 py-2 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
              />
            </div>
          )}
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
