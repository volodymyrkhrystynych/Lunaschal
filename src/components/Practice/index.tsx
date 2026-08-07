import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type PracticeSnippet } from '../../hooks/api';
import type { TypingStats } from '../../lib/practice';
import { DrillSession } from './DrillSession';
import { SessionSummary, type DrillResult } from './SessionSummary';
import { StatsPanel } from './StatsPanel';

const LANGUAGES = ['react', 'javascript', 'html', 'css', 'dom'] as const;
const FEEDBACK_DELAY_MS = 900;
const DECK_SIZE = 10;

export function Practice() {
  const [language, setLanguage] = useState('');
  const [category, setCategory] = useState('');
  // Fetched one at a time, not as a pre-shuffled batch: submitting an attempt
  // changes that snippet's priority score, so the next snippet has to be
  // re-ranked against the latest progress rather than served from a deck
  // that was already fixed before any of it was practiced.
  const [queue, setQueue] = useState<PracticeSnippet[]>([]);
  const [index, setIndex] = useState(0);
  const [noSnippets, setNoSnippets] = useState(false);
  const [results, setResults] = useState<DrillResult[]>([]);
  const [feedback, setFeedback] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const needsNext = !noSnippets && index === queue.length && index < DECK_SIZE;

  const { data: nextSnippets } = useQuery({
    queryKey: ['practice', 'next', language, category, index],
    queryFn: () =>
      api.practice.session({
        language: language || undefined,
        category: category || undefined,
        size: 1,
      }),
    enabled: needsNext,
  });

  useEffect(() => {
    if (!needsNext || nextSnippets === undefined) return;
    if (nextSnippets.length === 0) {
      setNoSnippets(true);
    } else {
      setQueue(q => (q.length === index ? [...q, ...nextSnippets] : q));
    }
  }, [nextSnippets, needsNext, index]);

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
    setQueue([]);
    setIndex(0);
    setNoSnippets(false);
    setResults([]);
  }

  function updateFilter(next: { language?: string; category?: string }) {
    if (next.language !== undefined) setLanguage(next.language);
    if (next.category !== undefined) setCategory(next.category);
    startNewSession();
  }

  const current = queue[index];
  const empty = noSnippets && queue.length === 0;
  const finished = !noSnippets && queue.length > 0 && index >= DECK_SIZE;

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

        {!current && !empty && !finished && (
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
              {index + 1} of {DECK_SIZE}
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
