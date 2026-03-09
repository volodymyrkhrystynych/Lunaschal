import { useState } from 'react';
import { trpc } from '../hooks/trpc';

type Mode = 'browse' | 'review' | 'create';

export function Flashcards() {
  const [mode, setMode] = useState<Mode>('browse');
  const [currentCardIndex, setCurrentCardIndex] = useState(0);
  const [showAnswer, setShowAnswer] = useState(false);
  const [newCard, setNewCard] = useState({ front: '', back: '' });
  const [flipAnimation, setFlipAnimation] = useState(false);

  const utils = trpc.useUtils();

  const { data: allCards } = trpc.flashcard.list.useQuery({});
  const { data: dueCards, refetch: refetchDue } = trpc.flashcard.getDue.useQuery();
  const { data: stats } = trpc.flashcard.getStats.useQuery();

  const createCard = trpc.flashcard.create.useMutation({
    onSuccess: () => {
      utils.flashcard.list.invalidate();
      setNewCard({ front: '', back: '' });
    },
  });

  const reviewCard = trpc.flashcard.review.useMutation({
    onSuccess: () => {
      refetchDue();
      utils.flashcard.getStats.invalidate();
      setFlipAnimation(true);
      setTimeout(() => {
        setShowAnswer(false);
        setFlipAnimation(false);
        if (dueCards && currentCardIndex < dueCards.length - 1) {
          setCurrentCardIndex(currentCardIndex + 1);
        } else {
          setCurrentCardIndex(0);
        }
      }, 200);
    },
  });

  const deleteCard = trpc.flashcard.delete.useMutation({
    onSuccess: () => {
      utils.flashcard.list.invalidate();
      utils.flashcard.getDue.invalidate();
    },
  });

  const handleCreate = () => {
    if (!newCard.front.trim() || !newCard.back.trim()) return;
    createCard.mutate(newCard);
  };

  const handleReview = (grade: number) => {
    if (!dueCards || dueCards.length === 0) return;
    const card = dueCards[currentCardIndex];
    reviewCard.mutate({ id: card.id, grade });
  };

  const currentCard = dueCards?.[currentCardIndex];

  // Grade descriptions for SM-2
  const grades = [
    { value: 0, label: 'Again', color: 'bg-red-500' },
    { value: 1, label: 'Hard', color: 'bg-orange-500' },
    { value: 2, label: 'Good', color: 'bg-yellow-500' },
    { value: 3, label: 'Easy', color: 'bg-green-500' },
  ];

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold text-[var(--color-text)]">Flashcards</h1>
        <div className="flex gap-2">
          <button
            onClick={() => setMode('browse')}
            className={`px-3 py-1 rounded ${
              mode === 'browse'
                ? 'bg-[var(--color-primary)] text-white'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
            }`}
          >
            Browse
          </button>
          <button
            onClick={() => {
              setMode('review');
              setCurrentCardIndex(0);
              setShowAnswer(false);
            }}
            className={`px-3 py-1 rounded ${
              mode === 'review'
                ? 'bg-[var(--color-primary)] text-white'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
            }`}
          >
            Review ({dueCards?.length || 0})
          </button>
          <button
            onClick={() => setMode('create')}
            className={`px-3 py-1 rounded ${
              mode === 'create'
                ? 'bg-[var(--color-primary)] text-white'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
            }`}
          >
            + Create
          </button>
        </div>
      </div>

      {/* Stats Bar */}
      {stats && (
        <div className="mb-4 grid grid-cols-4 gap-4">
          <div className="bg-[var(--color-surface)] rounded-lg border border-white/10 p-4 text-center">
            <div className="text-2xl font-bold text-[var(--color-text)]">{stats.total}</div>
            <div className="text-sm text-[var(--color-text-muted)]">Total Cards</div>
          </div>
          <div className="bg-[var(--color-surface)] rounded-lg border border-white/10 p-4 text-center">
            <div className="text-2xl font-bold text-orange-400">{stats.due}</div>
            <div className="text-sm text-[var(--color-text-muted)]">Due Today</div>
          </div>
          <div className="bg-[var(--color-surface)] rounded-lg border border-white/10 p-4 text-center">
            <div className="text-2xl font-bold text-blue-400">{stats.learning}</div>
            <div className="text-sm text-[var(--color-text-muted)]">Learning</div>
          </div>
          <div className="bg-[var(--color-surface)] rounded-lg border border-white/10 p-4 text-center">
            <div className="text-2xl font-bold text-green-400">{stats.mastered}</div>
            <div className="text-sm text-[var(--color-text-muted)]">Mastered</div>
          </div>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {mode === 'browse' && (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {allCards?.map((card) => {
              const isDue = new Date(card.nextReview) <= new Date();
              const isMastered = (card.interval || 0) >= 21;
              return (
                <div
                  key={card.id}
                  className={`p-4 bg-[var(--color-surface)] rounded-lg border transition-colors ${
                    isDue ? 'border-orange-500/50' : isMastered ? 'border-green-500/30' : 'border-white/10'
                  }`}
                >
                  {/* Status badge */}
                  <div className="flex items-center justify-between mb-3">
                    {isDue ? (
                      <span className="px-2 py-0.5 text-xs bg-orange-500/20 text-orange-400 rounded">Due</span>
                    ) : isMastered ? (
                      <span className="px-2 py-0.5 text-xs bg-green-500/20 text-green-400 rounded">Mastered</span>
                    ) : (
                      <span className="px-2 py-0.5 text-xs bg-blue-500/20 text-blue-400 rounded">Learning</span>
                    )}
                    <button
                      onClick={() => deleteCard.mutate({ id: card.id })}
                      className="text-xs text-red-400 hover:text-red-300"
                    >
                      Delete
                    </button>
                  </div>

                  <div className="text-xs text-[var(--color-text-muted)] mb-1 uppercase tracking-wide">Question</div>
                  <div className="text-[var(--color-text)] mb-4 line-clamp-3">{card.front}</div>
                  <div className="text-xs text-[var(--color-text-muted)] mb-1 uppercase tracking-wide">Answer</div>
                  <div className="text-[var(--color-text)] mb-4 line-clamp-3">{card.back}</div>

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
                <div className="text-lg">No flashcards yet</div>
                <div className="mt-2">Create some manually or generate them from journal entries!</div>
              </div>
            )}
          </div>
        )}

        {mode === 'review' && (
          <div className="max-w-lg mx-auto">
            {currentCard ? (
              <div className={`bg-[var(--color-surface)] rounded-lg border border-white/10 overflow-hidden transition-transform duration-200 ${flipAnimation ? 'scale-95 opacity-50' : 'scale-100 opacity-100'}`}>
                {/* Progress bar */}
                <div className="h-1 bg-white/5">
                  <div
                    className="h-full bg-[var(--color-primary)] transition-all duration-300"
                    style={{ width: `${((currentCardIndex + 1) / (dueCards?.length || 1)) * 100}%` }}
                  />
                </div>

                <div className="p-8">
                  <div className="text-center mb-6 text-sm text-[var(--color-text-muted)]">
                    Card {currentCardIndex + 1} of {dueCards?.length}
                  </div>

                  <div className="min-h-[200px] flex flex-col items-center justify-center">
                    <div className="text-xs text-[var(--color-text-muted)] mb-2 uppercase tracking-wide">
                      {showAnswer ? 'Answer' : 'Question'}
                    </div>
                    <div className="text-xl text-[var(--color-text)] text-center leading-relaxed">
                      {showAnswer ? currentCard.back : currentCard.front}
                    </div>
                  </div>

                  {!showAnswer ? (
                    <button
                      onClick={() => setShowAnswer(true)}
                      className="w-full py-3 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors font-medium"
                    >
                      Show Answer
                    </button>
                  ) : (
                    <div className="space-y-3">
                      <div className="text-center text-sm text-[var(--color-text-muted)]">
                        How well did you know this?
                      </div>
                      <div className="grid grid-cols-4 gap-2">
                        {grades.map((grade) => (
                          <button
                            key={grade.value}
                            onClick={() => handleReview(grade.value)}
                            disabled={reviewCard.isPending}
                            className={`py-3 ${grade.color} text-white rounded-lg hover:opacity-80 transition-all disabled:opacity-50 font-medium`}
                          >
                            {grade.label}
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
                <div className="text-[var(--color-text-muted)]">No cards due for review right now.</div>
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
                <label className="block text-sm text-[var(--color-text-muted)] mb-2">
                  Front (Question)
                </label>
                <textarea
                  value={newCard.front}
                  onChange={(e) => setNewCard({ ...newCard, front: e.target.value })}
                  placeholder="What do you want to remember?"
                  rows={3}
                  className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg p-3 resize-none focus:outline-none focus:border-[var(--color-primary)]"
                />
              </div>

              <div className="mb-6">
                <label className="block text-sm text-[var(--color-text-muted)] mb-2">
                  Back (Answer)
                </label>
                <textarea
                  value={newCard.back}
                  onChange={(e) => setNewCard({ ...newCard, back: e.target.value })}
                  placeholder="The answer or explanation"
                  rows={3}
                  className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg p-3 resize-none focus:outline-none focus:border-[var(--color-primary)]"
                />
              </div>

              <button
                onClick={handleCreate}
                disabled={!newCard.front.trim() || !newCard.back.trim() || createCard.isPending}
                className="w-full py-3 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors disabled:opacity-50"
              >
                Create Flashcard
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
