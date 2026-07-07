import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../hooks/api';
import { parseTagsInput } from '../lib/tags';

type Mode = 'browse' | 'review' | 'create';

const pillClass = (active: boolean) =>
  `px-3 py-1 text-sm rounded-full border transition-colors ${active ? 'bg-[var(--color-primary)] border-[var(--color-primary)] text-white' : 'border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`;

export function Flashcards() {
  const [mode, setMode] = useState<Mode>('browse');
  const [currentCardIndex, setCurrentCardIndex] = useState(0);
  const [showAnswer, setShowAnswer] = useState(false);
  const [newCard, setNewCard] = useState({ front: '', back: '', tags: '' });
  const [flipAnimation, setFlipAnimation] = useState(false);
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: allCards } = useQuery({
    queryKey: ['flashcard', 'list', selectedTag],
    queryFn: () => api.flashcard.list(selectedTag ? { tag: selectedTag } : undefined),
  });
  const { data: dueCards, refetch: refetchDue } = useQuery({
    queryKey: ['flashcard', 'due', selectedTag],
    queryFn: () => api.flashcard.getDue(selectedTag ?? undefined),
  });
  const { data: stats } = useQuery({
    queryKey: ['flashcard', 'stats', selectedTag],
    queryFn: () => api.flashcard.getStats(selectedTag ?? undefined),
  });
  const { data: tags } = useQuery({ queryKey: ['flashcard', 'tags'], queryFn: api.flashcard.getTags });

  // If the selected tag vanishes (its last card deleted or retagged), drop the
  // filter instead of showing a permanently empty deck with no pill highlighted.
  useEffect(() => {
    if (selectedTag && tags && !tags.some((t) => t.name === selectedTag)) {
      setSelectedTag(null);
    }
  }, [tags, selectedTag]);

  const selectTag = (tag: string | null) => {
    setSelectedTag(tag);
    setCurrentCardIndex(0);
    setShowAnswer(false);
  };

  const createCard = useMutation({
    mutationFn: (card: { front: string; back: string; tags: string }) =>
      api.flashcard.create({ front: card.front, back: card.back, tags: parseTagsInput(card.tags) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['flashcard'] });
      setNewCard((prev) => ({ front: '', back: '', tags: prev.tags }));
    },
  });

  const reviewCard = useMutation({
    mutationFn: ({ id, grade }: { id: string; grade: number }) => api.flashcard.review(id, grade),
    onSuccess: async () => {
      queryClient.invalidateQueries({ queryKey: ['flashcard', 'list'] });
      queryClient.invalidateQueries({ queryKey: ['flashcard', 'stats'] });
      setFlipAnimation(true);
      // The graded card leaves the due list (its next review is at least a day
      // out), so the next card sits at the same index in the refetched list —
      // only wrap to 0 when the index falls off the end.
      const { data: freshDue } = await refetchDue();
      setTimeout(() => {
        setShowAnswer(false);
        setFlipAnimation(false);
        setCurrentCardIndex((prev) => (freshDue && prev < freshDue.length ? prev : 0));
      }, 200);
    },
  });

  const deleteCard = useMutation({
    mutationFn: (id: string) => api.flashcard.delete(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['flashcard'] }),
  });

  const currentCard = dueCards?.[currentCardIndex];

  const grades = [
    { value: 0, label: 'Again', color: 'bg-red-500' },
    { value: 1, label: 'Hard', color: 'bg-orange-500' },
    { value: 2, label: 'Good', color: 'bg-yellow-500' },
    { value: 3, label: 'Easy', color: 'bg-green-500' },
  ];

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold text-[var(--color-text)]">Flashcards</h1>
        <div className="flex gap-2">
          <button onClick={() => setMode('browse')}
            className={`px-3 py-1 rounded ${mode === 'browse' ? 'bg-[var(--color-primary)] text-white' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}>
            Browse
          </button>
          <button onClick={() => { setMode('review'); setCurrentCardIndex(0); setShowAnswer(false); }}
            className={`px-3 py-1 rounded ${mode === 'review' ? 'bg-[var(--color-primary)] text-white' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}>
            Review ({dueCards?.length || 0})
          </button>
          <button onClick={() => setMode('create')}
            className={`px-3 py-1 rounded ${mode === 'create' ? 'bg-[var(--color-primary)] text-white' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}>
            + Create
          </button>
        </div>
      </div>

      {tags && tags.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-2">
          <button onClick={() => selectTag(null)} className={pillClass(!selectedTag)}>
            All
          </button>
          {tags.map((t) => (
            <button key={t.name} onClick={() => selectTag(selectedTag === t.name ? null : t.name)}
              className={pillClass(selectedTag === t.name)}>
              #{t.name} <span className="opacity-60">{t.count}</span>
            </button>
          ))}
        </div>
      )}

      {stats && (
        <div className="mb-4 grid grid-cols-4 gap-4">
          {[
            { label: 'Total Cards', value: stats.total, color: 'text-[var(--color-text)]' },
            { label: 'Due Today', value: stats.due, color: 'text-orange-400' },
            { label: 'Learning', value: stats.learning, color: 'text-blue-400' },
            { label: 'Mastered', value: stats.mastered, color: 'text-green-400' },
          ].map(({ label, value, color }) => (
            <div key={label} className="bg-[var(--color-surface)] rounded-lg border border-white/10 p-4 text-center">
              <div className={`text-2xl font-bold ${color}`}>{value}</div>
              <div className="text-sm text-[var(--color-text-muted)]">{label}</div>
            </div>
          ))}
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {mode === 'browse' && (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {allCards?.map((card) => {
              const isDue = new Date(card.nextReview) <= new Date();
              const isMastered = (card.interval || 0) >= 21;
              return (
                <div key={card.id} className={`p-4 bg-[var(--color-surface)] rounded-lg border transition-colors ${isDue ? 'border-orange-500/50' : isMastered ? 'border-green-500/30' : 'border-white/10'}`}>
                  <div className="flex items-center justify-between mb-3">
                    {isDue ? (
                      <span className="px-2 py-0.5 text-xs bg-orange-500/20 text-orange-400 rounded">Due</span>
                    ) : isMastered ? (
                      <span className="px-2 py-0.5 text-xs bg-green-500/20 text-green-400 rounded">Mastered</span>
                    ) : (
                      <span className="px-2 py-0.5 text-xs bg-blue-500/20 text-blue-400 rounded">Learning</span>
                    )}
                    <button onClick={() => deleteCard.mutate(card.id)} className="text-xs text-red-400 hover:text-red-300">Delete</button>
                  </div>
                  <div className="text-xs text-[var(--color-text-muted)] mb-1 uppercase tracking-wide">Question</div>
                  <div className="text-[var(--color-text)] mb-4 line-clamp-3">{card.front}</div>
                  <div className="text-xs text-[var(--color-text-muted)] mb-1 uppercase tracking-wide">Answer</div>
                  <div className="text-[var(--color-text)] mb-4 line-clamp-3">{card.back}</div>
                  {card.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mb-3">
                      {card.tags.map((t) => (
                        <button key={t} onClick={() => selectTag(t)}
                          className="px-2 py-0.5 text-xs rounded-full bg-white/5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
                          #{t}
                        </button>
                      ))}
                    </div>
                  )}
                  <div className="flex items-center justify-between text-xs text-[var(--color-text-muted)] pt-2 border-t border-white/5">
                    <span>Next: {new Date(card.nextReview).toLocaleDateString()}</span>
                    <span>Interval: {card.interval || 0}d</span>
                  </div>
                </div>
              );
            })}
            {(!allCards || allCards.length === 0) && (
              <div className="col-span-full text-center text-[var(--color-text-muted)] py-12">
                <div className="text-4xl mb-4">📚</div>
                {selectedTag ? (
                  <>
                    <div className="text-lg">No cards tagged <span className="text-[var(--color-text)]">#{selectedTag}</span></div>
                    <div className="mt-2">Pick another tag or All to see the rest of your cards.</div>
                  </>
                ) : (
                  <>
                    <div className="text-lg">No flashcards yet</div>
                    <div className="mt-2">Create some manually or generate them from journal entries!</div>
                  </>
                )}
              </div>
            )}
          </div>
        )}

        {mode === 'review' && (
          <div className="max-w-lg mx-auto">
            {currentCard ? (
              <div className={`bg-[var(--color-surface)] rounded-lg border border-white/10 overflow-hidden transition-transform duration-200 ${flipAnimation ? 'scale-95 opacity-50' : 'scale-100 opacity-100'}`}>
                <div className="h-1 bg-white/5">
                  <div className="h-full bg-[var(--color-primary)] transition-all duration-300"
                    style={{ width: `${((currentCardIndex + 1) / (dueCards?.length || 1)) * 100}%` }} />
                </div>
                <div className="p-8">
                  <div className="text-center mb-6 text-sm text-[var(--color-text-muted)]">Card {currentCardIndex + 1} of {dueCards?.length}</div>
                  <div className="min-h-[200px] flex flex-col items-center justify-center">
                    <div className="text-xs text-[var(--color-text-muted)] mb-2 uppercase tracking-wide">{showAnswer ? 'Answer' : 'Question'}</div>
                    <div className="text-xl text-[var(--color-text)] text-center leading-relaxed">
                      {showAnswer ? currentCard.back : currentCard.front}
                    </div>
                  </div>
                  {!showAnswer ? (
                    <button onClick={() => setShowAnswer(true)}
                      className="w-full py-3 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors font-medium">
                      Show Answer
                    </button>
                  ) : (
                    <div className="space-y-3">
                      <div className="text-center text-sm text-[var(--color-text-muted)]">How well did you know this?</div>
                      <div className="grid grid-cols-4 gap-2">
                        {grades.map((g) => (
                          <button key={g.value} onClick={() => reviewCard.mutate({ id: currentCard.id, grade: g.value })}
                            disabled={reviewCard.isPending}
                            className={`py-3 ${g.color} text-white rounded-lg hover:opacity-80 transition-all disabled:opacity-50 font-medium`}>
                            {g.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="text-center py-16">
                <div className="text-6xl mb-4">🎉</div>
                <div className="text-2xl font-semibold text-[var(--color-text)] mb-2">All caught up!</div>
                <div className="text-[var(--color-text-muted)]">
                  {selectedTag ? <>No <span className="text-[var(--color-text)]">#{selectedTag}</span> cards due for review right now.</> : 'No cards due for review right now.'}
                </div>
                {stats && stats.total > 0 && (
                  <div className="mt-6 text-sm text-[var(--color-text-muted)]">
                    You have {stats.mastered} mastered cards and {stats.learning} still learning.
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {mode === 'create' && (
          <div className="max-w-lg mx-auto">
            <div className="bg-[var(--color-surface)] rounded-lg border border-white/10 p-6">
              <div className="mb-4">
                <label className="block text-sm text-[var(--color-text-muted)] mb-2">Front (Question)</label>
                <textarea value={newCard.front} onChange={(e) => setNewCard({ ...newCard, front: e.target.value })}
                  placeholder="What do you want to remember?" rows={3}
                  className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg p-3 resize-none focus:outline-none focus:border-[var(--color-primary)]" />
              </div>
              <div className="mb-4">
                <label className="block text-sm text-[var(--color-text-muted)] mb-2">Back (Answer)</label>
                <textarea value={newCard.back} onChange={(e) => setNewCard({ ...newCard, back: e.target.value })}
                  placeholder="The answer or explanation" rows={3}
                  className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg p-3 resize-none focus:outline-none focus:border-[var(--color-primary)]" />
              </div>
              <div className="mb-6">
                <label className="block text-sm text-[var(--color-text-muted)] mb-2">Tags (comma-separated, optional)</label>
                <input value={newCard.tags} onChange={(e) => setNewCard({ ...newCard, tags: e.target.value })}
                  placeholder="javascript, python, ..."
                  className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg p-3 focus:outline-none focus:border-[var(--color-primary)]" />
              </div>
              <button onClick={() => createCard.mutate(newCard)} disabled={!newCard.front.trim() || !newCard.back.trim() || createCard.isPending}
                className="w-full py-3 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors disabled:opacity-50">
                Create Flashcard
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
