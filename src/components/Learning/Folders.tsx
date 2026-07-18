import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type McpServer } from '../../hooks/api';

export function Folders() {
  const [newFolder, setNewFolder] = useState('');
  const queryClient = useQueryClient();

  const { data: folders } = useQuery({ queryKey: ['learning', 'folders'], queryFn: api.learning.listFolders });
  const { data: servers } = useQuery({ queryKey: ['learning', 'mcp-servers'], queryFn: api.learning.listMcpServers });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['learning'] });

  const createFolder = useMutation({
    mutationFn: () => api.learning.createFolder(newFolder.trim()),
    onSuccess: () => { setNewFolder(''); invalidate(); },
  });
  const bindProvider = useMutation({
    mutationFn: ({ id, providerId }: { id: string; providerId: string | null }) =>
      api.learning.updateFolder(id, { evidenceProviderId: providerId }),
    onSuccess: invalidate,
  });
  const deleteFolder = useMutation({
    mutationFn: (id: string) => api.learning.deleteFolder(id),
    onSuccess: invalidate,
  });

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="bg-[var(--color-surface)] rounded-lg border border-white/10 p-5">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-1">Folders</h2>
        <p className="text-sm text-[var(--color-text-muted)] mb-4">
          A folder scopes which evidence provider verification uses. Cards without a folder can't be verified.
        </p>

        <div className="space-y-2 mb-4">
          {folders?.map((f) => (
            <div key={f.id} className="flex items-center gap-3 border border-white/10 rounded-lg px-3 py-2">
              <span className="text-[var(--color-text)] flex-1">{f.name}</span>
              <span className="text-xs text-[var(--color-text-muted)]">
                {f.activeCount} cards{f.pendingCount > 0 && ` · ${f.pendingCount} queued`}
              </span>
              <select
                value={f.evidenceProviderId ?? ''}
                onChange={(e) => bindProvider.mutate({ id: f.id, providerId: e.target.value || null })}
                className="bg-[var(--color-bg)] text-sm text-[var(--color-text)] border border-white/10 rounded px-2 py-1 focus:outline-none">
                <option value="">No evidence provider</option>
                {servers?.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
              <button onClick={() => deleteFolder.mutate(f.id)}
                className="text-xs text-red-400 hover:text-red-300">Delete</button>
            </div>
          ))}
          {(!folders || folders.length === 0) && (
            <p className="text-sm text-[var(--color-text-muted)]">No folders yet.</p>
          )}
        </div>

        <div className="flex gap-2">
          <input
            value={newFolder}
            onChange={(e) => setNewFolder(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && newFolder.trim()) createFolder.mutate(); }}
            placeholder="New folder name…"
            className="flex-1 bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]"
          />
          <button
            onClick={() => createFolder.mutate()}
            disabled={!newFolder.trim() || createFolder.isPending}
            className="px-4 py-2 text-sm bg-[var(--color-primary)] text-white rounded-lg hover:opacity-80 disabled:opacity-50">
            Add
          </button>
        </div>
        {createFolder.isError && (
          <p className="text-xs text-red-400 mt-2">
            {createFolder.error instanceof Error ? createFolder.error.message : 'Failed'}
          </p>
        )}
      </div>

      <McpServers servers={servers ?? []} />
    </div>
  );
}

function McpServers({ servers }: { servers: McpServer[] }) {
  const [form, setForm] = useState({ name: '', transport: 'stdio' as 'stdio' | 'http', command: '', args: '', url: '' });
  const [testResult, setTestResult] = useState<Record<string, string>>({});
  const queryClient = useQueryClient();

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['learning', 'mcp-servers'] });

  const create = useMutation({
    mutationFn: () => api.learning.createMcpServer({
      name: form.name.trim(),
      transport: form.transport,
      command: form.command.trim() || undefined,
      args: form.args.trim() ? form.args.trim().split(/\s+/) : undefined,
      url: form.url.trim() || undefined,
    }),
    onSuccess: () => {
      setForm({ name: '', transport: 'stdio', command: '', args: '', url: '' });
      invalidate();
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.learning.deleteMcpServer(id),
    onSuccess: invalidate,
  });

  const test = useMutation({
    mutationFn: (id: string) => api.learning.testMcpServer(id),
    onSuccess: (r, id) => {
      setTestResult((prev) => ({
        ...prev,
        [id]: r.ok ? `✓ ${r.tools.length} tools: ${r.tools.join(', ')}` : `✗ ${r.error || 'failed'}`,
      }));
    },
  });

  const valid = form.name.trim() &&
    (form.transport === 'stdio' ? form.command.trim() : form.url.trim());

  return (
    <div className="bg-[var(--color-surface)] rounded-lg border border-white/10 p-5">
      <h2 className="text-lg font-semibold text-[var(--color-text)] mb-1">Evidence providers (MCP servers)</h2>
      <p className="text-sm text-[var(--color-text-muted)] mb-4">
        e.g. Context7 for current library docs: command <code className="text-xs bg-white/5 px-1 rounded">npx</code>,
        args <code className="text-xs bg-white/5 px-1 rounded">-y @upstash/context7-mcp</code>
      </p>

      <div className="space-y-2 mb-4">
        {servers.map((s) => (
          <div key={s.id} className="border border-white/10 rounded-lg px-3 py-2">
            <div className="flex items-center gap-3">
              <span className="text-[var(--color-text)] flex-1">{s.name}</span>
              <span className="text-xs text-[var(--color-text-muted)]">
                {s.transport === 'stdio' ? `${s.command} ${s.args.join(' ')}` : s.url}
              </span>
              <button onClick={() => test.mutate(s.id)} disabled={test.isPending}
                className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50">
                {test.isPending && test.variables === s.id ? 'Testing…' : 'Test'}
              </button>
              <button onClick={() => remove.mutate(s.id)}
                className="text-xs text-red-400 hover:text-red-300">Delete</button>
            </div>
            {testResult[s.id] && (
              <div className={`text-xs mt-1 ${testResult[s.id].startsWith('✓') ? 'text-green-400' : 'text-red-400'}`}>
                {testResult[s.id]}
              </div>
            )}
          </div>
        ))}
        {servers.length === 0 && (
          <p className="text-sm text-[var(--color-text-muted)]">No MCP servers configured.</p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2 mb-2">
        <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
          placeholder="Name (e.g. context7)"
          className="bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]" />
        <select value={form.transport}
          onChange={(e) => setForm({ ...form, transport: e.target.value as 'stdio' | 'http' })}
          className="bg-[var(--color-bg)] text-sm text-[var(--color-text)] border border-white/10 rounded-lg px-3 py-2 focus:outline-none">
          <option value="stdio">stdio (local command)</option>
          <option value="http">http (remote URL)</option>
        </select>
        {form.transport === 'stdio' ? (
          <>
            <input value={form.command} onChange={(e) => setForm({ ...form, command: e.target.value })}
              placeholder="Command (e.g. npx)"
              className="bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]" />
            <input value={form.args} onChange={(e) => setForm({ ...form, args: e.target.value })}
              placeholder="Args (space-separated)"
              className="bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]" />
          </>
        ) : (
          <input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })}
            placeholder="URL (e.g. https://…/mcp)"
            className="col-span-2 bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]" />
        )}
      </div>
      <button onClick={() => create.mutate()} disabled={!valid || create.isPending}
        className="px-4 py-2 text-sm bg-white/10 text-[var(--color-text)] rounded-lg hover:bg-white/20 disabled:opacity-50">
        Add server
      </button>
      {create.isError && (
        <p className="text-xs text-red-400 mt-2">
          {create.error instanceof Error ? create.error.message : 'Failed'}
        </p>
      )}
    </div>
  );
}
