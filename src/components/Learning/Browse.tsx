import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type LearningCard } from '../../hooks/api';
import { parseTagsInput } from '../../lib/tags';
import { VerificationPanel } from './VerificationPanel';

interface Props {
  folderId: string | null;
  tag: string | null;
  onSelectTag: (tag: string | null) => void;
}

export function Browse({ folderId, tag, onSelectTag }: Props) {
  const [editing, setEditing] = useState<LearningCard | null>(null);
  const [verifying, setVerifying] = useState<LearningCard | null>(null);
  const queryClient = useQueryClient();

  const { data: cards } = useQuery({
    queryKey: ['learning', 'cards', folderId, tag],
    queryFn: () => api.learning.listCards({
      folderId: folderId ?? undefined,
      tag: tag ?? undefined,
      limit: 200,
    }),
  });

  const deleteCard = useMutation({
    mutationFn: (id: string) => api.learning.deleteCard(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['learning'] }),
  });

  return (
    <>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {cards?.map((card) => {
          const isDue = card.due !== null && new Date(card.due) <= new Date();
          return (
            <div key={card.id}
              className={`p-4 bg-[var(--color-surface)] rounded-lg border transition-colors ${isDue ? 'border-orange-500/50' : 'border-white/10'}`}>
              <div className="flex items-center justify-between mb-3">
                {isDue ? (
                  <span className="px-2 py-0.5 text-xs bg-orange-500/20 text-orange-400 rounded">Due</span>
                ) : (
                  <span className="px-2 py-0.5 text-xs bg-blue-500/20 text-blue-400 rounded">Scheduled</span>
                )}
                <div className="flex gap-2">
                  <button onClick={() => setVerifying(card)}
                    className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Verify</button>
                  <button onClick={() => setEditing(card)}
                    className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Edit</button>
                  <button onClick={() => deleteCard.mutate(card.id)}
                    className="text-xs text-red-400 hover:text-red-300">Delete</button>
                </div>
              </div>
              <div className="text-xs text-[var(--color-text-muted)] mb-1 uppercase tracking-wide">Question</div>
              <div className="text-[var(--color-text)] mb-4 line-clamp-3">{card.question}</div>
              <div className="text-xs text-[var(--color-text-muted)] mb-1 uppercase tracking-wide">Answer</div>
              <div className="text-[var(--color-text)] mb-4 line-clamp-3">{card.answer}</div>
              {card.tags.length > 0 && (
                <div className="flex flex-wrap gap-1 mb-3">
                  {card.tags.map((t) => (
                    <button key={t} onClick={() => onSelectTag(t)}
                      className="px-2 py-0.5 text-xs rounded-full bg-white/5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
                      #{t}
                    </button>
                  ))}
                </div>
              )}
              <div className="flex items-center justify-between text-xs text-[var(--color-text-muted)] pt-2 border-t border-white/5">
                <span>{card.due ? `Next: ${new Date(card.due).toLocaleDateString()}` : 'Not scheduled'}</span>
                {card.revisedFrom && <span title="This card supersedes an earlier version">rev</span>}
              </div>
            </div>
          );
        })}
        {(!cards || cards.length === 0) && (
          <div className="col-span-full text-center text-[var(--color-text-muted)] py-12">
            <div className="text-4xl mb-4">📚</div>
            <div className="text-lg">No cards here yet</div>
            <div className="mt-2">Brain-dump something in + Create, or generate cards from a journal entry.</div>
          </div>
        )}
      </div>

      {editing && (
        <EditCardDialog
          card={editing}
          onClose={() => setEditing(null)}
        />
      )}
      {verifying && (
        <VerificationPanel
          card={verifying}
          onClose={() => setVerifying(null)}
          onRevised={() => {
            setVerifying(null);
            queryClient.invalidateQueries({ queryKey: ['learning'] });
          }}
        />
      )}
    </>
  );
}

function EditCardDialog({ card, onClose }: { card: LearningCard; onClose: () => void }) {
  const [question, setQuestion] = useState(card.question);
  const [answer, setAnswer] = useState(card.answer);
  const [tags, setTags] = useState(card.tags.join(', '));
  const [note, setNote] = useState('');
  const queryClient = useQueryClient();

  const { data: revisions } = useQuery({
    queryKey: ['learning', 'revisions', card.id],
    queryFn: () => api.learning.getRevisions(card.id),
  });

  const contentChanged = question.trim() !== card.question || answer.trim() !== card.answer;
  const tagsChanged = tags !== card.tags.join(', ');

  const save = useMutation({
    mutationFn: async () => {
      // Content edits on an active card go through the revise flow (versioning +
      // semantic-gated FSRS reset); tag-only edits are a plain update.
      if (tagsChanged) {
        await api.learning.updateCard(card.id, { tags: parseTagsInput(tags) });
      }
      if (contentChanged) {
        await api.learning.revise(card.id, {
          answer: answer.trim(),
          question: question.trim(),
          triggerType: 'manual_edit',
          note: note.trim() || undefined,
        });
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['learning'] });
      onClose();
    },
  });

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-[var(--color-surface)] rounded-lg p-6 max-w-lg w-full mx-4 max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">Edit card</h2>
        <div className="space-y-3">
          <textarea value={question} onChange={(e) => setQuestion(e.target.value)} rows={2}
            className="w-full bg-transparent text-[var(--color-text)] border border-white/10 rounded-lg p-3 resize-none focus:outline-none focus:border-[var(--color-primary)]" />
          <textarea value={answer} onChange={(e) => setAnswer(e.target.value)} rows={3}
            className="w-full bg-transparent text-[var(--color-text)] border border-white/10 rounded-lg p-3 resize-none focus:outline-none focus:border-[var(--color-primary)]" />
          <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="Tags (comma-separated)"
            className="w-full bg-transparent text-sm text-[var(--color-text)] border border-white/10 rounded-lg p-2.5 focus:outline-none focus:border-[var(--color-primary)]" />
          {contentChanged && (
            <>
              <input value={note} onChange={(e) => setNote(e.target.value)}
                placeholder="Why the change? (optional, kept in the revision log)"
                className="w-full bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg p-2.5 focus:outline-none focus:border-[var(--color-primary)]" />
              <p className="text-xs text-[var(--color-text-muted)]">
                Content edits create a new version. If the change is semantic the card's schedule
                resets so the correction gets reinforced; cosmetic edits keep the schedule.
              </p>
            </>
          )}
          <div className="flex gap-2">
            <button
              onClick={() => save.mutate()}
              disabled={(!contentChanged && !tagsChanged) || !question.trim() || !answer.trim() || save.isPending}
              className="px-4 py-2 text-sm bg-[var(--color-primary)] text-white rounded-lg hover:opacity-80 font-medium disabled:opacity-50">
              {save.isPending ? 'Saving…' : 'Save'}
            </button>
            <button onClick={onClose}
              className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
              Cancel
            </button>
          </div>
        </div>

        {revisions && revisions.length > 0 && (
          <div className="mt-5 pt-4 border-t border-white/10 space-y-3">
            <div className="text-xs text-[var(--color-text-muted)] uppercase tracking-wide">Revision history</div>
            {revisions.map((r) => (
              <div key={r.id} className="text-sm border-l-2 border-white/10 pl-3">
                <div className="text-xs text-[var(--color-text-muted)]">
                  {new Date(r.createdAt).toLocaleString()} · {r.triggerType === 'web_verification' ? 'verified' : 'manual edit'} ·{' '}
                  {r.isSemantic ? 'semantic (schedule reset)' : 'cosmetic'}
                </div>
                <div className="text-[var(--color-text-muted)] line-through">{r.oldAnswer}</div>
                <div className="text-[var(--color-text)]">{r.newAnswer}</div>
                {r.note && <div className="text-xs text-[var(--color-text-muted)] mt-0.5">"{r.note}"</div>}
                {r.sources.length > 0 && (
                  <div className="text-xs text-[var(--color-text-muted)] mt-0.5">
                    {r.sources.map((s, i) => <span key={i}>[{s.title}] </span>)}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
