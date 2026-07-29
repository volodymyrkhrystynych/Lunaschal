import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { api } from '../../hooks/api';

export function KnowledgeBaseSection() {
  const [syncProgress, setSyncProgress] = useState<string | null>(null);

  const { data: ragConfigured } = useQuery({
    queryKey: ['rag', 'configured'],
    queryFn: api.rag.isConfigured,
  });
  const { data: stats } = useQuery({
    queryKey: ['rag', 'stats'],
    queryFn: api.rag.getStats,
  });

  const syncAll = useMutation({
    mutationFn: api.rag.syncAll,
    onMutate: () => setSyncProgress('Starting sync...'),
    onSuccess: result => {
      setSyncProgress(
        `Synced ${result.synced} entries (${result.chunks} chunks)`
      );
      setTimeout(() => setSyncProgress(null), 5000);
    },
    onError: (error: Error) => setSyncProgress(`Error: ${error.message}`),
  });

  return (
    <section className="mb-8">
      <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">
        Knowledge Base
      </h2>
      <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
        <p className="text-sm text-[var(--color-text-muted)] mb-4">
          The knowledge base uses AI embeddings to enable semantic search across
          your journal entries. This allows the AI to find relevant context from
          your notes when chatting.
        </p>
        {!ragConfigured ? (
          <div className="bg-yellow-900/30 border border-yellow-600/50 rounded-lg p-3 text-yellow-200 text-sm">
            Embeddings require a configured Ollama server. Set the Ollama URL
            above to enable semantic search.
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div className="bg-white/5 rounded-lg p-3">
                <div className="text-2xl font-bold text-[var(--color-text)]">
                  {stats?.totalJournals || 0}
                </div>
                <div className="text-sm text-[var(--color-text-muted)]">
                  Journal Entries
                </div>
              </div>
              <div className="bg-white/5 rounded-lg p-3">
                <div className="text-2xl font-bold text-green-400">
                  {stats?.isConfigured ? 'Active' : 'Inactive'}
                </div>
                <div className="text-sm text-[var(--color-text-muted)]">
                  Embedding Status
                </div>
              </div>
            </div>
            <div className="flex items-center gap-4">
              <button
                onClick={() => syncAll.mutate()}
                disabled={syncAll.isPending}
                className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
              >
                {syncAll.isPending ? 'Syncing...' : 'Rebuild Knowledge Base'}
              </button>
              {syncProgress && (
                <span className="text-sm text-[var(--color-text-muted)]">
                  {syncProgress}
                </span>
              )}
            </div>
            <p className="text-xs text-[var(--color-text-muted)] mt-3">
              New journal entries are automatically indexed. Use "Rebuild" to
              re-index all entries after changing AI providers.
            </p>
          </>
        )}
      </div>
    </section>
  );
}
