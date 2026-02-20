import { useState } from 'react';
import { trpc } from '../hooks/trpc';

type Provider = 'openai' | 'gemini' | 'ollama';

export function Settings() {
  const [openaiKey, setOpenaiKey] = useState('');
  const [googleKey, setGoogleKey] = useState('');
  const [ollamaUrl, setOllamaUrl] = useState('http://localhost:11434');
  const [ollamaModel, setOllamaModel] = useState('llama3.2');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const utils = trpc.useUtils();
  const { data: settings, isLoading } = trpc.settings.get.useQuery();

  const updateAI = trpc.settings.updateAI.useMutation({
    onSuccess: () => {
      utils.settings.get.invalidate();
      setMessage({ type: 'success', text: 'Settings saved successfully' });
      setTimeout(() => setMessage(null), 3000);
    },
    onError: (error) => {
      setMessage({ type: 'error', text: error.message });
    },
  });

  const changePassword = trpc.settings.changePassword.useMutation({
    onSuccess: () => {
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      setMessage({ type: 'success', text: 'Password changed successfully' });
      setTimeout(() => setMessage(null), 3000);
    },
    onError: (error) => {
      setMessage({ type: 'error', text: error.message });
    },
  });

  const handleProviderChange = (provider: Provider) => {
    updateAI.mutate({ aiProvider: provider });
  };

  const handleSaveOpenAI = () => {
    if (!openaiKey.trim()) return;
    updateAI.mutate({ openaiApiKey: openaiKey, aiProvider: 'openai' });
    setOpenaiKey('');
  };

  const handleSaveGoogle = () => {
    if (!googleKey.trim()) return;
    updateAI.mutate({ googleApiKey: googleKey, aiProvider: 'gemini' });
    setGoogleKey('');
  };

  const handleSaveOllama = () => {
    updateAI.mutate({
      ollamaUrl,
      ollamaModel,
      aiProvider: 'ollama',
    });
  };

  const handleChangePassword = () => {
    if (newPassword !== confirmPassword) {
      setMessage({ type: 'error', text: 'Passwords do not match' });
      return;
    }
    if (newPassword.length < 8) {
      setMessage({ type: 'error', text: 'Password must be at least 8 characters' });
      return;
    }
    changePassword.mutate({ currentPassword, newPassword });
  };

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
        <div
          className={`mb-4 p-3 rounded-lg ${
            message.type === 'success'
              ? 'bg-green-900/30 border border-green-600/50 text-green-200'
              : 'bg-red-900/30 border border-red-600/50 text-red-200'
          }`}
        >
          {message.text}
        </div>
      )}

      {/* AI Provider Selection */}
      <section className="mb-8">
        <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">AI Provider</h2>

        <div className="grid gap-4 md:grid-cols-3">
          {/* OpenAI */}
          <div
            className={`p-4 bg-[var(--color-surface)] rounded-lg border transition-colors cursor-pointer ${
              settings?.aiProvider === 'openai'
                ? 'border-[var(--color-primary)]'
                : 'border-white/10 hover:border-white/20'
            }`}
            onClick={() => handleProviderChange('openai')}
          >
            <div className="flex items-center gap-2 mb-2">
              <div
                className={`w-3 h-3 rounded-full ${
                  settings?.aiProvider === 'openai' ? 'bg-[var(--color-primary)]' : 'bg-white/20'
                }`}
              />
              <h3 className="font-medium text-[var(--color-text)]">OpenAI</h3>
            </div>
            <p className="text-sm text-[var(--color-text-muted)] mb-3">
              GPT-4o and other OpenAI models
            </p>
            <div className="text-xs text-[var(--color-text-muted)]">
              {settings?.hasOpenaiKey ? '✓ API key configured' : '✗ No API key'}
            </div>
          </div>

          {/* Google Gemini */}
          <div
            className={`p-4 bg-[var(--color-surface)] rounded-lg border transition-colors cursor-pointer ${
              settings?.aiProvider === 'gemini'
                ? 'border-[var(--color-primary)]'
                : 'border-white/10 hover:border-white/20'
            }`}
            onClick={() => handleProviderChange('gemini')}
          >
            <div className="flex items-center gap-2 mb-2">
              <div
                className={`w-3 h-3 rounded-full ${
                  settings?.aiProvider === 'gemini' ? 'bg-[var(--color-primary)]' : 'bg-white/20'
                }`}
              />
              <h3 className="font-medium text-[var(--color-text)]">Google Gemini</h3>
            </div>
            <p className="text-sm text-[var(--color-text-muted)] mb-3">
              Gemini 2.0 Flash and other models
            </p>
            <div className="text-xs text-[var(--color-text-muted)]">
              {settings?.hasGoogleKey ? '✓ API key configured' : '✗ No API key'}
            </div>
          </div>

          {/* Ollama */}
          <div
            className={`p-4 bg-[var(--color-surface)] rounded-lg border transition-colors cursor-pointer ${
              settings?.aiProvider === 'ollama'
                ? 'border-[var(--color-primary)]'
                : 'border-white/10 hover:border-white/20'
            }`}
            onClick={() => handleProviderChange('ollama')}
          >
            <div className="flex items-center gap-2 mb-2">
              <div
                className={`w-3 h-3 rounded-full ${
                  settings?.aiProvider === 'ollama' ? 'bg-[var(--color-primary)]' : 'bg-white/20'
                }`}
              />
              <h3 className="font-medium text-[var(--color-text)]">Ollama (Local)</h3>
            </div>
            <p className="text-sm text-[var(--color-text-muted)] mb-3">
              Run AI models locally
            </p>
            <div className="text-xs text-[var(--color-text-muted)]">
              URL: {settings?.ollamaUrl || 'http://localhost:11434'}
            </div>
          </div>
        </div>
      </section>

      {/* API Key Configuration */}
      <section className="mb-8">
        <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">API Keys</h2>

        <div className="space-y-4">
          {/* OpenAI Key */}
          <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
            <h3 className="font-medium text-[var(--color-text)] mb-2">OpenAI API Key</h3>
            <div className="flex gap-2">
              <input
                type="password"
                value={openaiKey}
                onChange={(e) => setOpenaiKey(e.target.value)}
                placeholder={settings?.hasOpenaiKey ? '••••••••••••••••' : 'sk-...'}
                className="flex-1 bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]"
              />
              <button
                onClick={handleSaveOpenAI}
                disabled={!openaiKey.trim() || updateAI.isPending}
                className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
              >
                Save
              </button>
            </div>
          </div>

          {/* Google Key */}
          <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
            <h3 className="font-medium text-[var(--color-text)] mb-2">Google API Key</h3>
            <div className="flex gap-2">
              <input
                type="password"
                value={googleKey}
                onChange={(e) => setGoogleKey(e.target.value)}
                placeholder={settings?.hasGoogleKey ? '••••••••••••••••' : 'AIza...'}
                className="flex-1 bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]"
              />
              <button
                onClick={handleSaveGoogle}
                disabled={!googleKey.trim() || updateAI.isPending}
                className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
              >
                Save
              </button>
            </div>
          </div>

          {/* Ollama Config */}
          <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
            <h3 className="font-medium text-[var(--color-text)] mb-2">Ollama Configuration</h3>
            <div className="space-y-3">
              <div>
                <label className="text-sm text-[var(--color-text-muted)]">Server URL</label>
                <input
                  type="text"
                  value={ollamaUrl}
                  onChange={(e) => setOllamaUrl(e.target.value)}
                  placeholder="http://localhost:11434"
                  className="w-full bg-transparent text-[var(--color-text)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]"
                />
              </div>
              <div>
                <label className="text-sm text-[var(--color-text-muted)]">Model</label>
                <input
                  type="text"
                  value={ollamaModel}
                  onChange={(e) => setOllamaModel(e.target.value)}
                  placeholder="llama3.2"
                  className="w-full bg-transparent text-[var(--color-text)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]"
                />
              </div>
              <button
                onClick={handleSaveOllama}
                disabled={updateAI.isPending}
                className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
              >
                Save Ollama Settings
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* Change Password */}
      <section className="mb-8">
        <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">Change Password</h2>

        <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10 max-w-md">
          <div className="space-y-3">
            <div>
              <label className="text-sm text-[var(--color-text-muted)]">Current Password</label>
              <input
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                className="w-full bg-transparent text-[var(--color-text)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]"
              />
            </div>
            <div>
              <label className="text-sm text-[var(--color-text-muted)]">New Password</label>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="w-full bg-transparent text-[var(--color-text)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]"
              />
            </div>
            <div>
              <label className="text-sm text-[var(--color-text-muted)]">Confirm New Password</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="w-full bg-transparent text-[var(--color-text)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]"
              />
            </div>
            <button
              onClick={handleChangePassword}
              disabled={!currentPassword || !newPassword || !confirmPassword || changePassword.isPending}
              className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
            >
              Change Password
            </button>
          </div>
        </div>
      </section>

      {/* About */}
      <section>
        <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">About</h2>
        <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
          <p className="text-[var(--color-text)]">Lunaschal v0.1.0</p>
          <p className="text-sm text-[var(--color-text-muted)] mt-1">
            A privacy-first, self-hosted personal AI knowledge assistant.
          </p>
        </div>
      </section>
    </div>
  );
}
