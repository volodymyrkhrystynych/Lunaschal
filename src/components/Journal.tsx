import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../hooks/api';
import { ulid } from '../lib/ulid';
import {
  useJournalCreate,
  useJournalUpdate,
} from '../offline/mutationDefaults';
import { buildFeed } from '../lib/journalFeed';
import { useShortcuts, useShortcutScope } from '../shortcuts/ShortcutProvider';

interface JournalProps {
  /** Navigate to the fanfic reader (chip on entries linked to a fic chapter). */
  onOpenFic?: (target: { ficId: string; chapterId?: string }) => void;
}

export function Journal({ onOpenFic }: JournalProps = {}) {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCuratedTagId, setSelectedCuratedTagId] = useState<
    string | null
  >(null);
  const [showTranscriptions, setShowTranscriptions] = useState(false);
  const [copiedTranscriptionId, setCopiedTranscriptionId] = useState<
    string | null
  >(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState('');
  const [editTitle, setEditTitle] = useState('');
  const [newEntry, setNewEntry] = useState('');
  const [showNewEntry, setShowNewEntry] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [generatingFor, setGeneratingFor] = useState<string | null>(null);
  const [generationResult, setGenerationResult] = useState<{
    id: string;
    count: number;
  } | null>(null);
  const [polishingFor, setPolishingFor] = useState<string | null>(null);
  const [selIndex, setSelIndex] = useState(0);
  const queryClient = useQueryClient();
  const { level } = useShortcuts();

  const { data: curatedTags } = useQuery({
    queryKey: ['curatedTags'],
    queryFn: api.curatedTags.list,
  });

  const { data: entries, isLoading } = useQuery({
    queryKey: searchQuery
      ? ['journal', 'search', searchQuery]
      : ['journal', { curatedTagId: selectedCuratedTagId }],
    queryFn: () =>
      searchQuery
        ? api.journal.search(searchQuery)
        : api.journal.list({ curatedTagId: selectedCuratedTagId ?? undefined }),
  });

  // Transcriptions only interleave in the plain chronological view — FTS search
  // doesn't cover them and a tag-filtered view is a curation context.
  const transcriptionsVisible =
    showTranscriptions && !searchQuery && !selectedCuratedTagId;

  const { data: transcriptions } = useQuery({
    queryKey: ['transcriptions'],
    queryFn: () => api.transcriptions.list(),
    enabled: transcriptionsVisible,
  });

  useEffect(() => {
    const es = new EventSource('/api/journal/events');
    es.onmessage = () =>
      queryClient.invalidateQueries({ queryKey: ['journal'] });
    return () => es.close();
  }, [queryClient]);

  // Offline-queueable: optimistic insert + reconciling invalidation live in the
  // registered mutation defaults. The UI reset must happen on submit, NOT in
  // onSuccess — offline the mutation is paused and onSuccess never fires until
  // reconnect, which would leave the compose box open (showing a duplicate of
  // the entry the optimistic insert already added to the feed).
  const createEntry = useJournalCreate();
  const updateEntry = useJournalUpdate();

  const submitNewEntry = () => {
    if (!newEntry.trim()) return;
    createEntry.mutate({ id: ulid(), content: newEntry });
    setNewEntry('');
    setShowNewEntry(false);
  };

  const submitEdit = (id: string) => {
    updateEntry.mutate({ id, content: editContent, title: editTitle });
    setEditingId(null);
  };

  const deleteEntry = useMutation({
    mutationFn: (id: string) => api.journal.delete(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['journal'] }),
  });

  const deleteTranscription = useMutation({
    mutationFn: (id: string) => api.transcriptions.delete(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['transcriptions'] }),
  });

  const copyTranscription = async (id: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // navigator.clipboard needs a secure context; fall back for webviews that deny it
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    setCopiedTranscriptionId(id);
    setTimeout(
      () => setCopiedTranscriptionId(cur => (cur === id ? null : cur)),
      1500
    );
  };

  const polishEntry = useMutation({
    mutationFn: (id: string) => api.journal.polish(id),
    onMutate: id => setPolishingFor(id),
    onSettled: () => setPolishingFor(null),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['journal'] }),
  });

  const generateFlashcards = useMutation({
    mutationFn: ({ journalId }: { journalId: string }) =>
      api.learning.generateFromJournal(journalId),
    onSuccess: (result, vars) => {
      setGeneratingFor(null);
      setGenerationResult({ id: vars.journalId, count: result.count });
      queryClient.invalidateQueries({ queryKey: ['learning'] });
      setTimeout(() => setGenerationResult(null), 5000);
    },
    onError: () => setGeneratingFor(null),
  });

  useEffect(() => {
    setSelIndex(i => Math.min(i, Math.max((entries?.length ?? 1) - 1, 0)));
  }, [entries]);

  useShortcutScope(1, {
    next: () =>
      setSelIndex(i =>
        Math.min(i + 1, Math.max((entries?.length ?? 1) - 1, 0))
      ),
    prev: () => setSelIndex(i => Math.max(i - 1, 0)),
    create: () => setShowNewEntry(true),
    drillIn: () => {
      const entry = entries?.[selIndex];
      if (!entry) return false;
      setEditingId(entry.id);
      setEditContent(entry.content);
      setEditTitle(entry.title ?? '');
      return true;
    },
    drillOut: () => {
      if (!editingId) return false;
      setEditingId(null);
      return true;
    },
  });

  const formatDate = (date: string) =>
    new Intl.DateTimeFormat('en-US', {
      weekday: 'short',
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(date));

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold text-[var(--color-text)]">
          Journal
        </h1>
        <div className="flex gap-2">
          <button
            onClick={() => setShowDelete(!showDelete)}
            title={showDelete ? 'Hide delete buttons' : 'Show delete buttons'}
            className={`px-4 py-2 border rounded-lg transition-colors ${
              showDelete
                ? 'border-red-400/50 text-red-400 bg-red-500/10'
                : 'border-white/20 text-[var(--color-text-muted)] hover:bg-white/10'
            }`}
          >
            🗑
          </button>
          <button
            onClick={() => setShowNewEntry(!showNewEntry)}
            className="px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors"
          >
            + New Entry
          </button>
        </div>
      </div>

      <div className="mb-4">
        <input
          type="text"
          value={searchQuery}
          onChange={e => {
            setSearchQuery(e.target.value);
            setSelectedCuratedTagId(null);
          }}
          placeholder="Search entries..."
          className="w-full bg-[var(--color-surface)] border border-white/10 rounded-lg px-4 py-2 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
        />
        <div className="flex flex-wrap gap-1.5 mt-2">
          {curatedTags?.map(tag => (
            <button
              key={tag.id}
              onClick={() =>
                setSelectedCuratedTagId(
                  selectedCuratedTagId === tag.id ? null : tag.id
                )
              }
              className={`px-3 py-1 text-xs rounded-full border transition-colors ${
                selectedCuratedTagId === tag.id
                  ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                  : 'border-white/20 text-[var(--color-text-muted)] hover:border-white/40 hover:text-[var(--color-text)]'
              }`}
            >
              #{tag.name}
              {tag.entryCount > 0 && (
                <span className="ml-1 opacity-60">({tag.entryCount})</span>
              )}
            </button>
          ))}
          <button
            onClick={() => setShowTranscriptions(!showTranscriptions)}
            className={`px-3 py-1 text-xs rounded-full border transition-colors ${
              showTranscriptions
                ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                : 'border-white/20 text-[var(--color-text-muted)] hover:border-white/40 hover:text-[var(--color-text)]'
            }`}
          >
            Show transcriptions
          </button>
        </div>
      </div>

      {showNewEntry && (
        <div className="mb-4 p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
          <textarea
            value={newEntry}
            onChange={e => setNewEntry(e.target.value)}
            autoFocus
            onKeyDown={e => {
              if (e.key === 'Escape') {
                setShowNewEntry(false);
                return;
              }
              // Enter saves; Shift+Enter inserts a newline.
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submitNewEntry();
              }
            }}
            placeholder="Write your journal entry..."
            rows={4}
            className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none"
          />
          <div className="flex justify-end gap-2 mt-2">
            <button
              onClick={() => setShowNewEntry(false)}
              className="px-3 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              Cancel
            </button>
            <button
              onClick={submitNewEntry}
              disabled={!newEntry.trim()}
              className="px-3 py-1 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
            >
              Save
            </button>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto space-y-4">
        {isLoading && (
          <div className="text-[var(--color-text-muted)]">Loading...</div>
        )}

        {buildFeed(
          entries ?? [],
          transcriptionsVisible ? (transcriptions ?? []) : []
        ).map(item => {
          if (item.kind === 'transcription') {
            const t = item.transcription;
            return (
              <div
                key={t.id}
                className="p-3 bg-[var(--color-surface)]/50 rounded-lg border border-white/5 opacity-70"
              >
                <div className="flex items-start justify-between gap-2 mb-1">
                  <div className="flex items-baseline gap-2 min-w-0">
                    <span className="text-sm text-[var(--color-text-muted)] shrink-0">
                      {formatDate(t.createdAt)}
                    </span>
                    {t.app && (
                      <span className="px-2 py-0.5 text-xs rounded border border-white/20 text-[var(--color-text-muted)] bg-white/5 truncate">
                        {t.app}
                        {t.detail && ` · ${t.detail}`}
                      </span>
                    )}
                  </div>
                  <div className="flex gap-2 shrink-0">
                    <button
                      onClick={() => copyTranscription(t.id, t.text)}
                      className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                    >
                      {copiedTranscriptionId === t.id ? 'Copied!' : 'Copy'}
                    </button>
                    {showDelete && (
                      <button
                        onClick={() => deleteTranscription.mutate(t.id)}
                        className="text-sm text-red-400 hover:text-red-300"
                      >
                        Delete
                      </button>
                    )}
                  </div>
                </div>
                <div className="text-sm text-[var(--color-text-muted)] italic whitespace-pre-wrap">
                  {t.text}
                </div>
              </div>
            );
          }
          const { entry, entryIndex: idx } = item;
          return (
            <div
              key={entry.id}
              ref={el => {
                if (el && level >= 1 && idx === selIndex)
                  el.scrollIntoView({ block: 'nearest' });
              }}
              className={`p-4 bg-[var(--color-surface)] rounded-lg border ${
                level >= 1 && idx === selIndex
                  ? 'border-[var(--color-primary)]'
                  : 'border-white/10'
              }`}
            >
              <div className="flex items-start justify-between mb-2">
                <span className="text-sm text-[var(--color-text-muted)]">
                  {formatDate(entry.createdAt)}
                </span>
                <div className="flex gap-2">
                  {entry.rawContent && (
                    <button
                      onClick={() => polishEntry.mutate(entry.id)}
                      disabled={polishingFor === entry.id}
                      className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
                    >
                      {polishingFor === entry.id ? 'Polishing...' : 'Polish'}
                    </button>
                  )}
                  <button
                    onClick={() => {
                      setGeneratingFor(entry.id);
                      generateFlashcards.mutate({ journalId: entry.id });
                    }}
                    disabled={generatingFor === entry.id}
                    className="text-sm text-[var(--color-primary)] hover:text-[var(--color-primary)]/80 disabled:opacity-50"
                  >
                    {generatingFor === entry.id
                      ? 'Generating...'
                      : 'Flashcards'}
                  </button>
                  <button
                    onClick={() => {
                      setEditingId(entry.id);
                      setEditContent(entry.content);
                      setEditTitle(entry.title ?? '');
                    }}
                    className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                  >
                    Edit
                  </button>
                  {showDelete && (
                    <button
                      onClick={() => deleteEntry.mutate(entry.id)}
                      className="text-sm text-red-400 hover:text-red-300"
                    >
                      Delete
                    </button>
                  )}
                </div>
              </div>

              {generationResult?.id === entry.id && (
                <div className="mb-2 px-3 py-2 bg-green-500/10 border border-green-500/20 rounded text-sm text-green-400">
                  Queued {generationResult.count} cards for approval in the
                  Learning tab.
                </div>
              )}

              {editingId === entry.id ? (
                <div>
                  <input
                    value={editTitle}
                    onChange={e => setEditTitle(e.target.value)}
                    placeholder="Entry title..."
                    onKeyDown={e => {
                      if (e.key === 'Escape') setEditingId(null);
                    }}
                    className="w-full bg-transparent text-[var(--color-text)] font-medium focus:outline-none border border-white/10 rounded p-2 mb-2"
                  />
                  <textarea
                    value={editContent}
                    onChange={e => setEditContent(e.target.value)}
                    rows={4}
                    autoFocus
                    onKeyDown={e => {
                      if (e.key === 'Escape') setEditingId(null);
                    }}
                    className="w-full bg-transparent text-[var(--color-text)] resize-none focus:outline-none border border-white/10 rounded p-2"
                  />
                  <div className="flex justify-end gap-2 mt-2">
                    <button
                      onClick={() => setEditingId(null)}
                      className="px-3 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={() => submitEdit(entry.id)}
                      className="px-3 py-1 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
                    >
                      Save
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  {entry.title && (
                    <h3 className="text-base font-bold text-[var(--color-text)] mb-2">
                      {entry.title}
                    </h3>
                  )}
                  <div className="text-[var(--color-text)] whitespace-pre-wrap">
                    {entry.content}
                  </div>
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

              {((entry.ficRefs?.length ?? 0) > 0 ||
                entry.curatedTags?.length > 0 ||
                (entry.tags && JSON.parse(entry.tags).length > 0)) && (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {entry.ficRefs?.map(ref => (
                    <button
                      key={`f:${ref.ficId}:${ref.chapterId ?? ''}`}
                      onClick={() =>
                        onOpenFic?.({
                          ficId: ref.ficId,
                          chapterId: ref.chapterId ?? undefined,
                        })
                      }
                      title="Open in reader"
                      className="px-2 py-0.5 text-xs rounded border border-[var(--color-accent)]/40 text-[var(--color-accent)] bg-[var(--color-accent)]/10 hover:bg-[var(--color-accent)]/20 transition-colors"
                    >
                      📖 {ref.ficTitle}
                      {ref.chapterTitle ? ` · ${ref.chapterTitle}` : ''}
                    </button>
                  ))}
                  {entry.curatedTags?.map((tag: string) => (
                    <span
                      key={`c:${tag}`}
                      className="px-2 py-0.5 text-xs rounded border border-white/20 text-[var(--color-text-muted)] bg-white/5"
                    >
                      #{tag}
                    </span>
                  ))}
                  {entry.tags &&
                    JSON.parse(entry.tags).map((tag: string) => (
                      <span
                        key={tag}
                        className="px-2 py-0.5 text-xs rounded border border-[var(--color-primary)]/40 text-[var(--color-primary)] bg-[var(--color-primary)]/10"
                      >
                        {tag}
                      </span>
                    ))}
                </div>
              )}
            </div>
          );
        })}

        {entries?.length === 0 && !isLoading && (
          <div className="text-center text-[var(--color-text-muted)] py-12">
            {searchQuery
              ? 'No entries found'
              : 'No journal entries yet. Start writing!'}
          </div>
        )}
      </div>
    </div>
  );
}
