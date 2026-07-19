import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { useShortcutScope } from '../../shortcuts/ShortcutProvider';
import { MessageMarkdown } from '../MessageMarkdown';

interface Props {
  onExit: () => void;
}

const RATINGS = [
  { value: 1 as const, label: 'Again', color: 'bg-red-500' },
  { value: 2 as const, label: 'Hard', color: 'bg-orange-500' },
  { value: 3 as const, label: 'Good', color: 'bg-yellow-500' },
  { value: 4 as const, label: 'Easy', color: 'bg-green-500' },
];

export function NotebookReviewSession({ onExit }: Props) {
  const [index, setIndex] = useState(0);
  const queryClient = useQueryClient();

  const { data: due, refetch: refetchDue } = useQuery({
    queryKey: ['notebook', 'review', 'due'],
    queryFn: api.notebook.review.due,
  });

  const entry = due?.[index];

  const { data: fileContent } = useQuery({
    queryKey: ['notebook', 'files', 'read', entry?.path],
    queryFn: () => api.notebook.files.read(entry!.path),
    enabled: !!entry,
  });

  const rate = useMutation({
    mutationFn: (rating: 1 | 2 | 3 | 4) =>
      api.notebook.review.rate(entry!.path, rating),
    onSuccess: async () => {
      queryClient.invalidateQueries({
        queryKey: ['notebook', 'review', 'state'],
      });
      const { data: fresh } = await refetchDue();
      setIndex(prev => (fresh && prev < fresh.length ? prev : 0));
    },
  });

  // No drillOut here, matching Learning's ReviewSession: exiting review is a
  // mode switch (the "Back to Notebook" button), not a tree drill-out, and
  // nav.out's dispatch only checks the handler at the *current* level — since
  // nothing else registers a depth-1 scope while reviewing, a depth-2
  // drillOut would never actually be reachable via the keyboard here.
  useShortcutScope(2, {
    rate: rating => {
      if (entry && !rate.isPending) rate.mutate(rating);
    },
  });

  if (!entry) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center py-16">
          <div className="text-6xl mb-4">🎉</div>
          <div className="text-2xl font-semibold text-[var(--color-text)] mb-2">
            All caught up!
          </div>
          <div className="text-[var(--color-text-muted)] mb-6">
            No notebook pages due for review right now.
          </div>
          <button
            onClick={onExit}
            className="px-4 py-2 rounded-lg bg-white/10 hover:bg-white/20 text-[var(--color-text)] font-medium transition-colors"
          >
            Back to Notebook
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto py-8 px-4">
      <div className="max-w-xl mx-auto">
        <div className="bg-[var(--color-surface)] rounded-lg border border-white/10 overflow-hidden">
          <div className="h-1 bg-white/5">
            <div
              className="h-full bg-[var(--color-primary)] transition-all duration-300"
              style={{ width: `${((index + 1) / (due?.length || 1)) * 100}%` }}
            />
          </div>
          <div className="p-8">
            <div className="flex items-center justify-between mb-6 text-sm text-[var(--color-text-muted)]">
              <span>
                Page {index + 1} of {due?.length}
              </span>
              <span className="truncate max-w-[60%]">{entry.path}</span>
            </div>

            <div className="text-[var(--color-text)] leading-relaxed mb-6 text-left">
              <MessageMarkdown content={fileContent?.content ?? ''} />
            </div>

            <div className="text-center text-sm text-[var(--color-text-muted)] mb-3">
              How well did you know this?
            </div>
            <div className="grid grid-cols-4 gap-2">
              {RATINGS.map(r => (
                <button
                  key={r.value}
                  onClick={() => rate.mutate(r.value)}
                  disabled={rate.isPending}
                  className={`py-3 ${r.color} text-white rounded-lg hover:opacity-80 transition-all disabled:opacity-50 font-medium`}
                >
                  {r.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
