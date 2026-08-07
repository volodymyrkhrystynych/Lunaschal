import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type PracticeSnippet } from '../../hooks/api';
import type { TypingStats } from '../../lib/practice';
import { DrillSession } from './DrillSession';
import { SessionSummary, type DrillResult } from './SessionSummary';
import { StatsPanel } from './StatsPanel';

const LANGUAGES = ['react', 'javascript', 'html', 'css'] as const;
const FEEDBACK_DELAY_MS = 900;

export function Practice() {
  const [language, setLanguage] = useState('');
  const [category, setCategory] = useState('');
  // Snapshotted once per session so a background refetch (e.g. from another
  // tab) can't reorder or shrink the deck mid-pass, same as Learning's
  // ReviewSession.
  const [queue, setQueue] = useState<PracticeSnippet[] | null>(null);
  const [index, setIndex] = useState(0);
  const [results, setResults] = useState<DrillResult[]>([]);
  const [feedback, setFeedback] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: session, isLoading } = useQuery({
    queryKey: ['practice', 'session', language, category],
    queryFn: () =>
      api.practice.session({
        language: language || undefined,
        category: category || undefined,
      }),
    enabled: queue === null,
  });

  useEffect(() => {
    if (session && queue === null) {
      setQueue(session);
      setIndex(0);
      setResults([]);
    }
  }, [session, queue]);

  const submitAttempt = useMutation({
    mutationFn: api.practice.submitAttempt,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['practice', 'stats'] });
    },
  });

  function handleComplete(snippet: PracticeSnippet, stats: TypingStats) {
    submitAttempt.mutate(
      {
        snippetId: snippet.id,
        wpm: stats.wpm,
        accuracy: stats.accuracy,
        errorCount: stats.errorCount,
      },
      {
        onSuccess: result => {
          setResults(r => [...r, { snippet, stats, rating: result.rating }]);
          setFeedback(result.rating);
          setTimeout(() => {
            setFeedback(null);
            setIndex(i => i + 1);
          }, FEEDBACK_DELAY_MS);
        },
      }
    );
  }

  function startNewSession() {
    setQueue(null);
  }

  function updateFilter(next: { language?: string; category?: string }) {
    if (next.language !== undefined) setLanguage(next.language);
    if (next.category !== undefined) setCategory(next.category);
    setQueue(null);
  }

  const current = queue?.[index];
  const empty = queue !== null && queue.length === 0;
  const finished = queue !== null && queue.length > 0 && index >= queue.length;

  return (
    <div className="flex-1 overflow-y-auto p-4 md:p-6">
      <div className="max-w-2xl mx-auto flex flex-col gap-6">
        <h1 className="text-xl font-semibold text-[var(--color-text)]">
          Practice
        </h1>

        <div className="flex flex-wrap gap-2">
          <select
            value={language}
            onChange={e => updateFilter({ language: e.target.value })}
            className="px-3 py-2 rounded bg-[var(--color-bg)] border border-white/10 text-[var(--color-text)] text-sm focus:outline-none focus:border-[var(--color-primary)]"
          >
            <option value="">All languages</option>
            {LANGUAGES.map(l => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
          <input
            value={category}
            onChange={e => updateFilter({ category: e.target.value })}
            placeholder="category (optional)"
            className="px-3 py-2 rounded bg-[var(--color-bg)] border border-white/10 text-[var(--color-text)] text-sm placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
          />
        </div>

        {isLoading && queue === null && (
          <div className="text-[var(--color-text-muted)]">Loading…</div>
        )}

        {empty && (
          <div className="text-[var(--color-text-muted)]">
            No snippets match these filters.
          </div>
        )}

        {current && !finished && (
          <div className="relative">
            <DrillSession
              key={current.id}
              snippet={current}
              onComplete={stats => handleComplete(current, stats)}
            />
            {feedback && (
              <div className="absolute inset-0 flex items-center justify-center bg-black/40 rounded-lg pointer-events-none">
                <span className="text-2xl font-semibold text-white">
                  {feedback}
                </span>
              </div>
            )}
            <div className="mt-2 text-center text-xs text-[var(--color-text-muted)]">
              {index + 1} of {queue?.length}
            </div>
          </div>
        )}

        {finished && (
          <SessionSummary results={results} onRestart={startNewSession} />
        )}

        <StatsPanel />
      </div>
    </div>
  );
}
