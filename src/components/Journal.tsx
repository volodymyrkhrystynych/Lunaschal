import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../hooks/api';

export function Journal() {
  const [searchQuery, setSearchQuery] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState('');
  const [editTitle, setEditTitle] = useState('');
  const [newEntry, setNewEntry] = useState('');
  const [showNewEntry, setShowNewEntry] = useState(false);
  const [generatingFor, setGeneratingFor] = useState<string | null>(null);
  const [generationResult, setGenerationResult] = useState<{ id: string; count: number } | null>(null);
  const [polishingFor, setPolishingFor] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: entries, isLoading } = useQuery({
    queryKey: searchQuery ? ['journal', 'search', searchQuery] : ['journal'],
    queryFn: () => (searchQuery ? api.journal.search(searchQuery) : api.journal.list()),
  });

  useEffect(() => {
    const es = new EventSource('/api/journal/events');
    es.onmessage = () => queryClient.invalidateQueries({ queryKey: ['journal'] });
    return () => es.close();
  }, [queryClient]);

  const createEntry = useMutation({
    mutationFn: ({ content }: { content: string }) => api.journal.create({ content }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['journal'] });
      setNewEntry('');
      setShowNewEntry(false);
    },
  });

  const updateEntry = useMutation({
    mutationFn: ({ id, content, title }: { id: string; content: string; title: string }) =>
      api.journal.update(id, { content, title }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['journal'] });
      setEditingId(null);
    },
  });

  const deleteEntry = useMutation({
    mutationFn: (id: string) => api.journal.delete(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['journal'] }),
  });

  const polishEntry = useMutation({
    mutationFn: (id: string) => api.journal.polish(id),
    onMutate: (id) => setPolishingFor(id),
    onSettled: () => setPolishingFor(null),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['journal'] }),
  });

  const generateFlashcards = useMutation({
    mutationFn: ({ journalId }: { journalId: string }) => api.flashcard.generateFromJournal(journalId),
    onSuccess: (result, vars) => {
      setGeneratingFor(null);
      setGenerationResult({ id: vars.journalId, count: result.count });
      queryClient.invalidateQueries({ queryKey: ['flashcard'] });
      setTimeout(() => setGenerationResult(null), 5000);
    },
    onError: () => setGeneratingFor(null),
  });

  const formatDate = (date: string) => new Intl.DateTimeFormat('en-US', {
    weekday: 'short', year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  }).format(new Date(date));

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold text-[var(--color-text)]">Journal</h1>
        <button onClick={() => setShowNewEntry(!showNewEntry)}
          className="px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors">
          + New Entry
        </button>
      </div>

      <div className="mb-4">
        <input type="text" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search entries..."
          className="w-full bg-[var(--color-surface)] border border-white/10 rounded-lg px-4 py-2 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]" />
      </div>

      {showNewEntry && (
        <div className="mb-4 p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
          <textarea value={newEntry} onChange={(e) => setNewEntry(e.target.value)}
            placeholder="Write your journal entry..." rows={4}
            className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none" />
          <div className="flex justify-end gap-2 mt-2">
            <button onClick={() => setShowNewEntry(false)} className="px-3 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Cancel</button>
            <button onClick={() => createEntry.mutate({ content: newEntry })} disabled={!newEntry.trim() || createEntry.isPending}
              className="px-3 py-1 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">Save</button>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto space-y-4">
        {isLoading && <div className="text-[var(--color-text-muted)]">Loading...</div>}

        {entries?.map((entry) => (
          <div key={entry.id} className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
            <div className="flex items-start justify-between mb-2">
              <span className="text-sm text-[var(--color-text-muted)]">{formatDate(entry.createdAt)}</span>
              <div className="flex gap-2">
                {entry.rawContent && (
                  <button onClick={() => polishEntry.mutate(entry.id)}
                    disabled={polishingFor === entry.id}
                    className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50">
                    {polishingFor === entry.id ? 'Polishing...' : 'Polish'}
                  </button>
                )}
                <button onClick={() => { setGeneratingFor(entry.id); generateFlashcards.mutate({ journalId: entry.id }); }}
                  disabled={generatingFor === entry.id}
                  className="text-sm text-[var(--color-primary)] hover:text-[var(--color-primary)]/80 disabled:opacity-50">
                  {generatingFor === entry.id ? 'Generating...' : 'Flashcards'}
                </button>
                <button onClick={() => { setEditingId(entry.id); setEditContent(entry.content); setEditTitle(entry.title ?? ''); }}
                  className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Edit</button>
                <button onClick={() => deleteEntry.mutate(entry.id)} className="text-sm text-red-400 hover:text-red-300">Delete</button>
              </div>
            </div>

            {generationResult?.id === entry.id && (
              <div className="mb-2 px-3 py-2 bg-green-500/10 border border-green-500/20 rounded text-sm text-green-400">
                Created {generationResult.count} flashcards from this entry!
              </div>
            )}

            {editingId === entry.id ? (
              <div>
                <input value={editTitle} onChange={(e) => setEditTitle(e.target.value)}
                  placeholder="Entry title..."
                  className="w-full bg-transparent text-[var(--color-text)] font-medium focus:outline-none border border-white/10 rounded p-2 mb-2" />
                <textarea value={editContent} onChange={(e) => setEditContent(e.target.value)} rows={4}
                  className="w-full bg-transparent text-[var(--color-text)] resize-none focus:outline-none border border-white/10 rounded p-2" />
                <div className="flex justify-end gap-2 mt-2">
                  <button onClick={() => setEditingId(null)} className="px-3 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Cancel</button>
                  <button onClick={() => updateEntry.mutate({ id: entry.id, content: editContent, title: editTitle })} disabled={updateEntry.isPending}
                    className="px-3 py-1 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">Save</button>
                </div>
              </div>
            ) : (
              <>
                {entry.title && (
                  <h3 className="text-sm font-semibold text-[var(--color-text)] mb-1">{entry.title}</h3>
                )}
                <div className="text-[var(--color-text)] whitespace-pre-wrap">{entry.content}</div>
                {entry.rawContent && (
                  <details className="mt-3">
                    <summary className="text-xs text-[var(--color-text-muted)] cursor-pointer select-none hover:text-[var(--color-text)] transition-colors">
                      Original transcription
                    </summary>
                    <div className="mt-2 px-3 py-2 bg-white/5 rounded text-sm text-[var(--color-text-muted)] whitespace-pre-wrap italic">
                      {entry.rawContent}
                    </div>
                  </details>
                )}
              </>
            )}

            {entry.tags && (
              <div className="flex gap-2 mt-2">
                {JSON.parse(entry.tags).map((tag: string) => (
                  <span key={tag} className="px-2 py-0.5 text-xs bg-white/10 rounded text-[var(--color-text-muted)]">{tag}</span>
                ))}
              </div>
            )}
          </div>
        ))}

        {entries?.length === 0 && !isLoading && (
          <div className="text-center text-[var(--color-text-muted)] py-12">
            {searchQuery ? 'No entries found' : 'No journal entries yet. Start writing!'}
          </div>
        )}
      </div>
    </div>
  );
}
