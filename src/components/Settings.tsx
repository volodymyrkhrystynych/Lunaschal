import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../hooks/api';

type Provider = 'openai' | 'gemini' | 'ollama';

function KnowledgeBaseSection() {
  const [syncProgress, setSyncProgress] = useState<string | null>(null);

  const { data: ragConfigured } = useQuery({ queryKey: ['rag', 'configured'], queryFn: api.rag.isConfigured });
  const { data: stats } = useQuery({ queryKey: ['rag', 'stats'], queryFn: api.rag.getStats });

  const syncAll = useMutation({
    mutationFn: api.rag.syncAll,
    onMutate: () => setSyncProgress('Starting sync...'),
    onSuccess: (result) => {
      setSyncProgress(`Synced ${result.synced} entries (${result.chunks} chunks)`);
      setTimeout(() => setSyncProgress(null), 5000);
    },
    onError: (error: Error) => setSyncProgress(`Error: ${error.message}`),
  });

  return (
    <section className="mb-8">
      <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">Knowledge Base</h2>
      <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
        <p className="text-sm text-[var(--color-text-muted)] mb-4">
          The knowledge base uses AI embeddings to enable semantic search across your journal entries.
          This allows the AI to find relevant context from your notes when chatting.
        </p>
        {!ragConfigured ? (
          <div className="bg-yellow-900/30 border border-yellow-600/50 rounded-lg p-3 text-yellow-200 text-sm">
            Embeddings require OpenAI or Google API key. Configure one above to enable semantic search.
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div className="bg-white/5 rounded-lg p-3">
                <div className="text-2xl font-bold text-[var(--color-text)]">{stats?.totalJournals || 0}</div>
                <div className="text-sm text-[var(--color-text-muted)]">Journal Entries</div>
              </div>
              <div className="bg-white/5 rounded-lg p-3">
                <div className="text-2xl font-bold text-green-400">{stats?.isConfigured ? 'Active' : 'Inactive'}</div>
                <div className="text-sm text-[var(--color-text-muted)]">Embedding Status</div>
              </div>
            </div>
            <div className="flex items-center gap-4">
              <button onClick={() => syncAll.mutate()} disabled={syncAll.isPending}
                className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">
                {syncAll.isPending ? 'Syncing...' : 'Rebuild Knowledge Base'}
              </button>
              {syncProgress && <span className="text-sm text-[var(--color-text-muted)]">{syncProgress}</span>}
            </div>
            <p className="text-xs text-[var(--color-text-muted)] mt-3">
              New journal entries are automatically indexed. Use "Rebuild" to re-index all entries after changing AI providers.
            </p>
          </>
        )}
      </div>
    </section>
  );
}

export function Settings() {
  const [openaiKey, setOpenaiKey] = useState('');
  const [googleKey, setGoogleKey] = useState('');
  const [ollamaUrl, setOllamaUrl] = useState('http://localhost:11434');
  const [ollamaModel, setOllamaModel] = useState('llama3.2');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const queryClient = useQueryClient();

  const { data: settings, isLoading } = useQuery({ queryKey: ['settings'], queryFn: api.settings.get });

  const updateAI = useMutation({
    mutationFn: api.settings.updateAI,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      setMessage({ type: 'success', text: 'Settings saved successfully' });
      setTimeout(() => setMessage(null), 3000);
    },
    onError: (error: Error) => setMessage({ type: 'error', text: error.message }),
  });

  const changePassword = useMutation({
    mutationFn: api.settings.changePassword,
    onSuccess: () => {
      setCurrentPassword(''); setNewPassword(''); setConfirmPassword('');
      setMessage({ type: 'success', text: 'Password changed successfully' });
      setTimeout(() => setMessage(null), 3000);
    },
    onError: (error: Error) => setMessage({ type: 'error', text: error.message }),
  });

  const providers: { id: Provider; label: string; subtitle: string; status: string }[] = [
    { id: 'openai', label: 'OpenAI', subtitle: 'GPT-4o and other OpenAI models', status: settings?.hasOpenaiKey ? '✓ API key configured' : '✗ No API key' },
    { id: 'gemini', label: 'Google Gemini', subtitle: 'Gemini 2.0 Flash and other models', status: settings?.hasGoogleKey ? '✓ API key configured' : '✗ No API key' },
    { id: 'ollama', label: 'Ollama (Local)', subtitle: 'Run AI models locally', status: `URL: ${settings?.ollamaUrl || 'http://localhost:11434'}` },
  ];

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-[var(--color-text-muted)]">Loading...</div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-4">
      <h1 className="text-2xl font-semibold text-[var(--color-text)] mb-6">Settings</h1>

      {message && (
        <div className={`mb-4 p-3 rounded-lg ${message.type === 'success' ? 'bg-green-900/30 border border-green-600/50 text-green-200' : 'bg-red-900/30 border border-red-600/50 text-red-200'}`}>
          {message.text}
        </div>
      )}

      <section className="mb-8">
        <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">AI Provider</h2>
        <div className="grid gap-4 md:grid-cols-3">
          {providers.map((p) => (
            <div key={p.id} onClick={() => updateAI.mutate({ aiProvider: p.id })}
              className={`p-4 bg-[var(--color-surface)] rounded-lg border transition-colors cursor-pointer ${settings?.aiProvider === p.id ? 'border-[var(--color-primary)]' : 'border-white/10 hover:border-white/20'}`}>
              <div className="flex items-center gap-2 mb-2">
                <div className={`w-3 h-3 rounded-full ${settings?.aiProvider === p.id ? 'bg-[var(--color-primary)]' : 'bg-white/20'}`} />
                <h3 className="font-medium text-[var(--color-text)]">{p.label}</h3>
              </div>
              <p className="text-sm text-[var(--color-text-muted)] mb-3">{p.subtitle}</p>
              <div className="text-xs text-[var(--color-text-muted)]">{p.status}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="mb-8">
        <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">API Keys</h2>
        <div className="space-y-4">
          <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
            <h3 className="font-medium text-[var(--color-text)] mb-2">OpenAI API Key</h3>
            <div className="flex gap-2">
              <input type="password" value={openaiKey} onChange={(e) => setOpenaiKey(e.target.value)}
                placeholder={settings?.hasOpenaiKey ? '••••••••••••••••' : 'sk-...'}
                className="flex-1 bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]" />
              <button onClick={() => { updateAI.mutate({ openaiApiKey: openaiKey, aiProvider: 'openai' }); setOpenaiKey(''); }}
                disabled={!openaiKey.trim() || updateAI.isPending}
                className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">Save</button>
            </div>
          </div>

          <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
            <h3 className="font-medium text-[var(--color-text)] mb-2">Google API Key</h3>
            <div className="flex gap-2">
              <input type="password" value={googleKey} onChange={(e) => setGoogleKey(e.target.value)}
                placeholder={settings?.hasGoogleKey ? '••••••••••••••••' : 'AIza...'}
                className="flex-1 bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]" />
              <button onClick={() => { updateAI.mutate({ googleApiKey: googleKey, aiProvider: 'gemini' }); setGoogleKey(''); }}
                disabled={!googleKey.trim() || updateAI.isPending}
                className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">Save</button>
            </div>
          </div>

          <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
            <h3 className="font-medium text-[var(--color-text)] mb-2">Ollama Configuration</h3>
            <div className="space-y-3">
              <div>
                <label className="text-sm text-[var(--color-text-muted)]">Server URL</label>
                <input type="text" value={ollamaUrl} onChange={(e) => setOllamaUrl(e.target.value)}
                  placeholder="http://localhost:11434"
                  className="w-full bg-transparent text-[var(--color-text)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]" />
              </div>
              <div>
                <label className="text-sm text-[var(--color-text-muted)]">Model</label>
                <input type="text" value={ollamaModel} onChange={(e) => setOllamaModel(e.target.value)} placeholder="llama3.2"
                  className="w-full bg-transparent text-[var(--color-text)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]" />
              </div>
              <button onClick={() => updateAI.mutate({ ollamaUrl, ollamaModel, aiProvider: 'ollama' })} disabled={updateAI.isPending}
                className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">
                Save Ollama Settings
              </button>
            </div>
          </div>
        </div>
      </section>

      <KnowledgeBaseSection />

      <section className="mb-8">
        <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">Change Password</h2>
        <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10 max-w-md">
          <div className="space-y-3">
            {[
              { label: 'Current Password', value: currentPassword, onChange: setCurrentPassword },
              { label: 'New Password', value: newPassword, onChange: setNewPassword },
              { label: 'Confirm New Password', value: confirmPassword, onChange: setConfirmPassword },
            ].map(({ label, value, onChange }) => (
              <div key={label}>
                <label className="text-sm text-[var(--color-text-muted)]">{label}</label>
                <input type="password" value={value} onChange={(e) => onChange(e.target.value)}
                  className="w-full bg-transparent text-[var(--color-text)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]" />
              </div>
            ))}
            <button
              onClick={() => {
                if (newPassword !== confirmPassword) { setMessage({ type: 'error', text: 'Passwords do not match' }); return; }
                if (newPassword.length < 8) { setMessage({ type: 'error', text: 'Password must be at least 8 characters' }); return; }
                changePassword.mutate({ currentPassword, newPassword });
              }}
              disabled={!currentPassword || !newPassword || !confirmPassword || changePassword.isPending}
              className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">
              Change Password
            </button>
          </div>
        </div>
      </section>

      <section>
        <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">About</h2>
        <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
          <p className="text-[var(--color-text)]">Lunaschal v0.1.0</p>
          <p className="text-sm text-[var(--color-text-muted)] mt-1">A privacy-first, self-hosted personal AI knowledge assistant.</p>
        </div>
      </section>
    </div>
  );
}
