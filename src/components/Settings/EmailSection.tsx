import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';

export function EmailSection() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });
  const { data: oauthStatus } = useQuery({
    queryKey: ['email', 'oauthStatus'],
    queryFn: api.email.oauthStatus,
  });

  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [intervalInput, setIntervalInput] = useState('15');

  useEffect(() => {
    if (settings) {
      setIntervalInput(String(settings.emailSyncIntervalMinutes ?? 15));
    }
  }, [settings]);

  const updateAI = useMutation({
    mutationFn: api.settings.updateAI,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const disconnect = useMutation({
    mutationFn: api.email.disconnect,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['email', 'oauthStatus'] }),
  });

  const syncEnabled = settings?.emailSyncEnabled ?? true;

  const commitInterval = () => {
    const minutes = Math.max(1, parseInt(intervalInput, 10) || 15);
    setIntervalInput(String(minutes));
    updateAI.mutate({ emailSyncIntervalMinutes: minutes });
  };

  return (
    <>
      <div className="space-y-4">
        <p className="text-sm text-[var(--color-text-muted)]">
          Mirrors your Gmail inbox locally and classifies it (job applications,
          rejections, interview next steps). Requires a Google Cloud OAuth
          client — create one in Google Cloud Console with the Gmail API's
          read-only scope and register{' '}
          <code>{`${window.location.origin}/api/email/oauth/callback`}</code> as
          an authorized redirect URI.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="text-sm text-[var(--color-text-muted)]">
              OAuth Client ID
            </label>
            <input
              type="text"
              value={clientId}
              onChange={e => setClientId(e.target.value)}
              placeholder={
                settings?.hasGoogleOauthClient
                  ? '••••••••••••••••'
                  : '….apps.googleusercontent.com'
              }
              className="mt-1 w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
            />
          </div>
          <div>
            <label className="text-sm text-[var(--color-text-muted)]">
              OAuth Client Secret
            </label>
            <input
              type="password"
              value={clientSecret}
              onChange={e => setClientSecret(e.target.value)}
              placeholder={
                settings?.hasGoogleOauthClient ? '••••••••••••••••' : 'GOCSPX-…'
              }
              className="mt-1 w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
            />
          </div>
        </div>
        <button
          onClick={() => {
            updateAI.mutate({
              googleOauthClientId: clientId,
              googleOauthClientSecret: clientSecret,
            });
            setClientId('');
            setClientSecret('');
          }}
          disabled={!clientId.trim() || !clientSecret.trim()}
          className="px-3 py-1.5 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10 text-[var(--color-text)] disabled:opacity-50"
        >
          Save credentials
        </button>

        <div className="pt-2 border-t border-white/10 space-y-3">
          {oauthStatus?.connected ? (
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-[var(--color-text)]">
                  Connected as {oauthStatus.emailAddress}
                </p>
                <p className="text-xs text-[var(--color-text-muted)]">
                  {oauthStatus.lastSyncError
                    ? `Last sync error: ${oauthStatus.lastSyncError}`
                    : oauthStatus.lastSyncedAt
                      ? `Last synced ${oauthStatus.lastSyncedAt}`
                      : 'Not synced yet'}
                </p>
              </div>
              <button
                onClick={() => disconnect.mutate()}
                className="px-3 py-1.5 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10 text-[var(--color-text)]"
              >
                Disconnect
              </button>
            </div>
          ) : (
            <button
              onClick={() => {
                // Opens in a new tab rather than navigating the current view: on
                // desktop this still runs inside the PyWebView shell's embedded
                // Chromium, which Google's consent screen may reject as an
                // "disallowed_useragent". If that happens here, the fix is a
                // native webview.create_window / js_api bridge in main.py that
                // hands the URL to the OS's real default browser instead.
                window.open('/api/email/oauth/authorize', '_blank', 'noopener');
              }}
              disabled={!settings?.hasGoogleOauthClient}
              className="px-3 py-1.5 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10 text-[var(--color-text)] disabled:opacity-50"
            >
              Connect Gmail
            </button>
          )}
        </div>

        <label className="flex items-center gap-3 cursor-pointer select-none pt-2 border-t border-white/10">
          <div
            onClick={() => updateAI.mutate({ emailSyncEnabled: !syncEnabled })}
            className={`relative w-9 h-5 rounded-full transition-colors ${syncEnabled ? 'bg-[var(--color-primary)]' : 'bg-white/20'}`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${syncEnabled ? 'translate-x-4' : 'translate-x-0'}`}
            />
          </div>
          <span className="text-sm text-[var(--color-text)]">
            Enable background sync
          </span>
        </label>
        {syncEnabled && (
          <div>
            <label className="text-sm text-[var(--color-text-muted)]">
              Sync interval (minutes)
            </label>
            <input
              type="number"
              min={1}
              value={intervalInput}
              onChange={e => setIntervalInput(e.target.value)}
              onBlur={commitInterval}
              className="mt-1 w-32 bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
            />
          </div>
        )}
      </div>
    </>
  );
}
