import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  api,
  type AppSettings,
  type EmailAccountStatus,
  type EmailProvider,
} from '../../hooks/api';

function OAuthProviderBlock({
  provider,
  label,
  settings,
  account,
  clientIdField,
  clientSecretField,
  hasClientKey,
  clientIdPlaceholder,
  clientSecretPlaceholder,
  helpText,
  disconnectNote,
  onSaved,
  onDisconnected,
}: {
  provider: EmailProvider;
  label: string;
  settings: AppSettings | null | undefined;
  account: EmailAccountStatus | undefined;
  clientIdField: 'googleOauthClientId' | 'microsoftOauthClientId';
  clientSecretField: 'googleOauthClientSecret' | 'microsoftOauthClientSecret';
  hasClientKey: 'hasGoogleOauthClient' | 'hasMicrosoftOauthClient';
  clientIdPlaceholder: string;
  clientSecretPlaceholder: string;
  helpText: React.ReactNode;
  disconnectNote?: string;
  onSaved: () => void;
  onDisconnected: () => void;
}) {
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');

  const updateAI = useMutation({
    mutationFn: api.settings.updateAI,
    onSuccess: onSaved,
  });
  const disconnect = useMutation({
    mutationFn: () => api.email.disconnect(provider),
    onSuccess: onDisconnected,
  });

  const hasClient = settings?.[hasClientKey] ?? false;

  return (
    <div className="space-y-3">
      <p className="text-sm text-[var(--color-text-muted)]">{helpText}</p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="text-sm text-[var(--color-text-muted)]">
            {label} OAuth Client ID
          </label>
          <input
            type="text"
            value={clientId}
            onChange={e => setClientId(e.target.value)}
            placeholder={hasClient ? '••••••••••••••••' : clientIdPlaceholder}
            className="mt-1 w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
          />
        </div>
        <div>
          <label className="text-sm text-[var(--color-text-muted)]">
            {label} OAuth Client Secret
          </label>
          <input
            type="password"
            value={clientSecret}
            onChange={e => setClientSecret(e.target.value)}
            placeholder={
              hasClient ? '••••••••••••••••' : clientSecretPlaceholder
            }
            className="mt-1 w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
          />
        </div>
      </div>
      <button
        onClick={() => {
          updateAI.mutate({
            [clientIdField]: clientId,
            [clientSecretField]: clientSecret,
          });
          setClientId('');
          setClientSecret('');
        }}
        disabled={!clientId.trim() || !clientSecret.trim()}
        className="px-3 py-1.5 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10 text-[var(--color-text)] disabled:opacity-50"
      >
        Save credentials
      </button>

      <div className="pt-2 border-t border-white/10">
        {account?.connected ? (
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-[var(--color-text)]">
                Connected as {account.emailAddress}
              </p>
              <p className="text-xs text-[var(--color-text-muted)]">
                {account.lastSyncError
                  ? `Last sync error: ${account.lastSyncError}`
                  : account.lastSyncedAt
                    ? `Last synced ${account.lastSyncedAt}`
                    : 'Not synced yet'}
              </p>
            </div>
            <div className="text-right">
              <button
                onClick={() => disconnect.mutate()}
                className="px-3 py-1.5 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10 text-[var(--color-text)]"
              >
                Disconnect
              </button>
              {disconnectNote && (
                <p className="text-xs text-[var(--color-text-muted)] mt-1 max-w-xs">
                  {disconnectNote}
                </p>
              )}
            </div>
          </div>
        ) : (
          <button
            onClick={() => {
              // Opens in a new tab rather than navigating the current view: on
              // desktop this still runs inside the PyWebView shell's embedded
              // Chromium, which the provider's consent screen may reject as a
              // "disallowed_useragent". If that happens here, the fix is a
              // native webview.create_window / js_api bridge in main.py that
              // hands the URL to the OS's real default browser instead.
              window.open(
                `/api/email/oauth/authorize?provider=${provider}`,
                '_blank',
                'noopener'
              );
            }}
            disabled={!hasClient}
            className="px-3 py-1.5 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10 text-[var(--color-text)] disabled:opacity-50"
          >
            Connect {label}
          </button>
        )}
      </div>
    </div>
  );
}

function ImapBlock({
  account,
  onConnected,
  onDisconnected,
}: {
  account: EmailAccountStatus | undefined;
  onConnected: () => void;
  onDisconnected: () => void;
}) {
  const [host, setHost] = useState('');
  const [port, setPort] = useState('993');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [emailAddress, setEmailAddress] = useState('');

  const connect = useMutation({
    mutationFn: () =>
      api.email.connectImap({
        host: host.trim(),
        port: parseInt(port, 10) || 993,
        username: username.trim(),
        password,
        emailAddress: emailAddress.trim(),
      }),
    onSuccess: result => {
      if ('error' in result) return;
      setHost('');
      setPort('993');
      setUsername('');
      setPassword('');
      setEmailAddress('');
      onConnected();
    },
  });
  const disconnect = useMutation({
    mutationFn: () => api.email.disconnect('imap'),
    onSuccess: onDisconnected,
  });

  const connectError =
    connect.data && 'error' in connect.data ? connect.data.error : null;

  return (
    <div className="space-y-3">
      <p className="text-sm text-[var(--color-text-muted)]">
        Any other IMAP mailbox — Fastmail, Yahoo, a custom domain. Use an
        app-specific password where your provider offers one rather than your
        main account password.
      </p>

      {account?.connected ? (
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-[var(--color-text)]">
              Connected as {account.emailAddress}
            </p>
            <p className="text-xs text-[var(--color-text-muted)]">
              {account.lastSyncError
                ? `Last sync error: ${account.lastSyncError}`
                : account.lastSyncedAt
                  ? `Last synced ${account.lastSyncedAt}`
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
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="text-sm text-[var(--color-text-muted)]">
                Host
              </label>
              <input
                type="text"
                value={host}
                onChange={e => setHost(e.target.value)}
                placeholder="imap.example.com"
                className="mt-1 w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
              />
            </div>
            <div>
              <label className="text-sm text-[var(--color-text-muted)]">
                Port
              </label>
              <input
                type="number"
                value={port}
                onChange={e => setPort(e.target.value)}
                className="mt-1 w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
              />
            </div>
            <div>
              <label className="text-sm text-[var(--color-text-muted)]">
                Username
              </label>
              <input
                type="text"
                value={username}
                onChange={e => setUsername(e.target.value)}
                placeholder="me@example.com"
                className="mt-1 w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
              />
            </div>
            <div>
              <label className="text-sm text-[var(--color-text-muted)]">
                Password
              </label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                className="mt-1 w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
              />
            </div>
            <div className="sm:col-span-2">
              <label className="text-sm text-[var(--color-text-muted)]">
                Email address
              </label>
              <input
                type="text"
                value={emailAddress}
                onChange={e => setEmailAddress(e.target.value)}
                placeholder="me@example.com"
                className="mt-1 w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
              />
            </div>
          </div>
          {connectError && (
            <p className="text-xs text-red-400">{connectError}</p>
          )}
          <button
            onClick={() => connect.mutate()}
            disabled={
              !host.trim() ||
              !username.trim() ||
              !password.trim() ||
              !emailAddress.trim()
            }
            className="px-3 py-1.5 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10 text-[var(--color-text)] disabled:opacity-50"
          >
            Connect
          </button>
        </>
      )}
    </div>
  );
}

export function EmailSection() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });
  const { data: accounts } = useQuery({
    queryKey: ['email', 'accounts'],
    queryFn: api.email.accounts,
  });

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

  const syncEnabled = settings?.emailSyncEnabled ?? true;

  const commitInterval = () => {
    const minutes = Math.max(1, parseInt(intervalInput, 10) || 15);
    setIntervalInput(String(minutes));
    updateAI.mutate({ emailSyncIntervalMinutes: minutes });
  };

  const refreshAccounts = () =>
    queryClient.invalidateQueries({ queryKey: ['email', 'accounts'] });
  const gmailAccount = accounts?.find(a => a.provider === 'gmail');
  const outlookAccount = accounts?.find(a => a.provider === 'outlook');
  const imapAccount = accounts?.find(a => a.provider === 'imap');

  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--color-text-muted)]">
        Mirrors your connected email accounts locally and classifies them (job
        applications, rejections, interview next steps).
      </p>

      <div className="space-y-6">
        <OAuthProviderBlock
          provider="gmail"
          label="Gmail"
          settings={settings}
          account={gmailAccount}
          clientIdField="googleOauthClientId"
          clientSecretField="googleOauthClientSecret"
          hasClientKey="hasGoogleOauthClient"
          clientIdPlaceholder="….apps.googleusercontent.com"
          clientSecretPlaceholder="GOCSPX-…"
          helpText={
            <>
              Requires a Google Cloud OAuth client — create one in Google Cloud
              Console with the Gmail API's read-only scope and register{' '}
              <code>{`${window.location.origin}/api/email/oauth/callback`}</code>{' '}
              as an authorized redirect URI.
            </>
          }
          onSaved={() =>
            queryClient.invalidateQueries({ queryKey: ['settings'] })
          }
          onDisconnected={refreshAccounts}
        />

        <div className="pt-4 border-t border-white/10">
          <OAuthProviderBlock
            provider="outlook"
            label="Outlook"
            settings={settings}
            account={outlookAccount}
            clientIdField="microsoftOauthClientId"
            clientSecretField="microsoftOauthClientSecret"
            hasClientKey="hasMicrosoftOauthClient"
            clientIdPlaceholder="Application (client) ID"
            clientSecretPlaceholder="Client secret value"
            helpText={
              <>
                Connected over IMAP, not Microsoft Graph. Requires an Azure AD
                app registration with the <code>IMAP.AccessAsUser.All</code>{' '}
                delegated permission and{' '}
                <code>{`${window.location.origin}/api/email/oauth/callback`}</code>{' '}
                registered as a redirect URI.
              </>
            }
            disconnectNote="Removes local access only — to fully revoke, remove the app from your Microsoft account's app permissions."
            onSaved={() =>
              queryClient.invalidateQueries({ queryKey: ['settings'] })
            }
            onDisconnected={refreshAccounts}
          />
        </div>

        <div className="pt-4 border-t border-white/10">
          <ImapBlock
            account={imapAccount}
            onConnected={refreshAccounts}
            onDisconnected={refreshAccounts}
          />
        </div>
      </div>

      <label className="flex items-center gap-3 cursor-pointer select-none pt-4 border-t border-white/10">
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
  );
}
