import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type ApproveResult, type LearningCard } from '../../hooks/api';
import { useShortcuts, useShortcutScope } from '../../shortcuts/ShortcutProvider';

interface HintState {
  card: LearningCard;
  similar: { id: string; question: string; answer: string };
  score: number;
}

export function Queue() {
  const [regenFor, setRegenFor] = useState<string | null>(null);
  const [direction, setDirection] = useState('');
  const [hint, setHint] = useState<HintState | null>(null);
  const [selIndex, setSelIndex] = useState(0);
  const queryClient = useQueryClient();
  const { level } = useShortcuts();

  const { data: queue } = useQuery({ queryKey: ['learning', 'queue'], queryFn: api.learning.listQueue });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['learning'] });

  const approve = useMutation({
    mutationFn: ({ id, force }: { id: string; force?: boolean }) => api.learning.approve(id, force),
    onSuccess: (result: ApproveResult, { id }) => {
      if (result.status === 'duplicateHint' && result.similar) {
        const card = queue?.find((c) => c.id === id);
        if (card) setHint({ card, similar: result.similar, score: result.score ?? 0 });
        return;
      }
      setHint(null);
      invalidate();
    },
  });

  const deny = useMutation({
    mutationFn: (id: string) => api.learning.deny(id),
    onSuccess: () => { setHint(null); invalidate(); },
  });

  const deleteActive = useMutation({
    mutationFn: (id: string) => api.learning.deleteCard(id),
  });

  const regenerate = useMutation({
    mutationFn: ({ id, dir }: { id: string; dir: string }) => api.learning.regenerate(id, dir),
    onSuccess: () => {
      setRegenFor(null);
      setDirection('');
      invalidate();
    },
  });

  useEffect(() => {
    setSelIndex((i) => Math.min(i, Math.max((queue?.length ?? 1) - 1, 0)));
  }, [queue]);

  useShortcutScope(2, {
    next: () => setSelIndex((i) => Math.min(i + 1, Math.max((queue?.length ?? 1) - 1, 0))),
    prev: () => setSelIndex((i) => Math.max(i - 1, 0)),
    approve: () => {
      const c = queue?.[selIndex];
      if (c && !approve.isPending) approve.mutate({ id: c.id });
    },
    deny: () => {
      const c = queue?.[selIndex];
      if (c && !deny.isPending) deny.mutate(c.id);
    },
    annotate: () => {
      const c = queue?.[selIndex];
      if (c) { setRegenFor(c.id); setDirection(''); }
    },
    drillOut: () => {
      if (hint) { setHint(null); return true; }
      if (regenFor) { setRegenFor(null); setDirection(''); return true; }
      return false;
    },
  });

  if (!queue || queue.length === 0) {
    return (
      <div className="text-center text-[var(--color-text-muted)] py-12">
        <div className="text-4xl mb-4">📥</div>
        <div className="text-lg">The approval queue is empty</div>
        <div className="mt-2">Generate cards from a brain-dump, a journal entry, or chat — they land here first.</div>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto space-y-4">
      {queue.map((card, idx) => (
        <div key={card.id}
          ref={(el) => { if (el && level >= 2 && idx === selIndex) el.scrollIntoView({ block: 'nearest' }); }}
          className={`bg-[var(--color-surface)] rounded-lg border p-4 transition-colors ${
            level >= 2 && idx === selIndex ? 'border-[var(--color-primary)]' : 'border-white/10'
          }`}>
          <div className="flex items-center gap-2 mb-3">
            {card.derivedFrom && (
              <span className="px-2 py-0.5 text-xs bg-purple-500/20 text-purple-300 rounded">follow-up</span>
            )}
            {card.sourceType && (
              <span className="px-2 py-0.5 text-xs bg-white/5 text-[var(--color-text-muted)] rounded">{card.sourceType}</span>
            )}
            {card.tags.map((t) => (
              <span key={t} className="px-2 py-0.5 text-xs rounded-full bg-white/5 text-[var(--color-text-muted)]">#{t}</span>
            ))}
          </div>
          <div className="text-xs text-[var(--color-text-muted)] mb-1 uppercase tracking-wide">Question</div>
          <div className="text-[var(--color-text)] mb-3">{card.question}</div>
          <div className="text-xs text-[var(--color-text-muted)] mb-1 uppercase tracking-wide">Answer</div>
          <div className="text-[var(--color-text)] mb-4">{card.answer}</div>

          {regenFor === card.id ? (
            <div className="flex gap-2">
              <input
                value={direction}
                onChange={(e) => setDirection(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && direction.trim()) regenerate.mutate({ id: card.id, dir: direction.trim() });
                }}
                placeholder='e.g. "too broad, split it" or "wrong emphasis"'
                autoFocus
                className="flex-1 bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg px-3 py-1.5 focus:outline-none focus:border-[var(--color-primary)]"
              />
              <button
                onClick={() => regenerate.mutate({ id: card.id, dir: direction.trim() })}
                disabled={!direction.trim() || regenerate.isPending}
                className="px-3 py-1.5 text-sm bg-[var(--color-primary)] text-white rounded-lg hover:opacity-80 disabled:opacity-50">
                {regenerate.isPending ? 'Regenerating…' : 'Go'}
              </button>
              <button
                onClick={() => { setRegenFor(null); setDirection(''); }}
                className="px-3 py-1.5 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
                Cancel
              </button>
            </div>
          ) : (
            <div className="flex gap-2">
              <button
                onClick={() => approve.mutate({ id: card.id })}
                disabled={approve.isPending}
                className="px-3 py-1.5 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors disabled:opacity-50">
                Approve
              </button>
              <button
                onClick={() => setRegenFor(card.id)}
                className="px-3 py-1.5 text-sm bg-white/10 text-[var(--color-text)] rounded-lg hover:bg-white/20 transition-colors">
                Regenerate…
              </button>
              <button
                onClick={() => deny.mutate(card.id)}
                disabled={deny.isPending}
                className="px-3 py-1.5 text-sm text-red-400 hover:text-red-300 transition-colors">
                Deny
              </button>
            </div>
          )}
        </div>
      ))}

      {hint && (
        <DuplicateHintDialog
          hint={hint}
          onKeepBoth={() => approve.mutate({ id: hint.card.id, force: true })}
          onDeleteNew={() => deny.mutate(hint.card.id)}
          onReplaceOld={async () => {
            await deleteActive.mutateAsync(hint.similar.id);
            approve.mutate({ id: hint.card.id, force: true });
          }}
          onClose={() => setHint(null)}
        />
      )}
    </div>
  );
}

function DuplicateHintDialog({ hint, onKeepBoth, onDeleteNew, onReplaceOld, onClose }: {
  hint: HintState;
  onKeepBoth: () => void;
  onDeleteNew: () => void;
  onReplaceOld: () => void;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-[var(--color-surface)] rounded-lg p-6 max-w-lg w-full mx-4 max-h-[80vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-1">Possible duplicate</h2>
        <p className="text-sm text-[var(--color-text-muted)] mb-4">
          This answer is {(hint.score * 100).toFixed(0)}% similar to an existing card.
        </p>

        <div className="space-y-3 mb-5">
          <div className="border border-white/10 rounded-lg p-3">
            <div className="text-xs text-[var(--color-text-muted)] mb-1">New card</div>
            <div className="text-sm text-[var(--color-text)]">{hint.card.question}</div>
            <div className="text-sm text-[var(--color-text-muted)] mt-1">{hint.card.answer}</div>
          </div>
          <div className="border border-orange-500/30 rounded-lg p-3">
            <div className="text-xs text-orange-300 mb-1">Existing card</div>
            <div className="text-sm text-[var(--color-text)]">{hint.similar.question}</div>
            <div className="text-sm text-[var(--color-text-muted)] mt-1">{hint.similar.answer}</div>
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <button onClick={onKeepBoth}
            className="py-2 bg-[var(--color-primary)] text-white rounded-lg hover:opacity-80 text-sm font-medium">
            Keep both — they're distinct
          </button>
          <button onClick={onReplaceOld}
            className="py-2 bg-white/10 text-[var(--color-text)] rounded-lg hover:bg-white/20 text-sm">
            Keep the new one, delete the old
          </button>
          <button onClick={onDeleteNew}
            className="py-2 text-red-400 hover:text-red-300 text-sm">
            Delete the new card
          </button>
        </div>
      </div>
    </div>
  );
}
