import { useState } from 'react';
import { trpc } from '../hooks/trpc';

type Mode = 'browse' | 'review' | 'create';

export function Flashcards() {
  const [mode, setMode] = useState<Mode>('browse');
  const [currentCardIndex, setCurrentCardIndex] = useState(0);
  const [showAnswer, setShowAnswer] = useState(false);
  const [newCard, setNewCard] = useState({ front: '', back: '' });

  const utils = trpc.useUtils();

  const { data: allCards } = trpc.flashcard.list.useQuery({});
  const { data: dueCards, refetch: refetchDue } = trpc.flashcard.getDue.useQuery();

  const createCard = trpc.flashcard.create.useMutation({
    onSuccess: () => {
      utils.flashcard.list.invalidate();
      setNewCard({ front: '', back: '' });
    },
  });

  const reviewCard = trpc.flashcard.review.useMutation({
    onSuccess: () => {
      refetchDue();
      setShowAnswer(false);
      if (dueCards && currentCardIndex < dueCards.length - 1) {
        setCurrentCardIndex(currentCardIndex + 1);
      } else {
        setCurrentCardIndex(0);
      }
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

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {mode === 'browse' && (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {allCards?.map((card) => (
              <div
                key={card.id}
                className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10"
              >
                <div className="text-sm text-[var(--color-text-muted)] mb-2">Front</div>
                <div className="text-[var(--color-text)] mb-4">{card.front}</div>
                <div className="text-sm text-[var(--color-text-muted)] mb-2">Back</div>
                <div className="text-[var(--color-text)] mb-4">{card.back}</div>
                <div className="flex items-center justify-between text-sm">
                  <span className="text-[var(--color-text-muted)]">
                    Next: {new Date(card.nextReview).toLocaleDateString()}
                  </span>
                  <button
                    onClick={() => deleteCard.mutate({ id: card.id })}
                    className="text-red-400 hover:text-red-300"
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))}

            {(!allCards || allCards.length === 0) && (
              <div className="col-span-full text-center text-[var(--color-text-muted)] py-12">
                No flashcards yet. Create some to start learning!
              </div>
            )}
          </div>
        )}

        {mode === 'review' && (
          <div className="max-w-lg mx-auto">
            {currentCard ? (
              <div className="bg-[var(--color-surface)] rounded-lg border border-white/10 p-8">
                <div className="text-center mb-4 text-sm text-[var(--color-text-muted)]">
                  Card {currentCardIndex + 1} of {dueCards?.length}
                </div>

                <div className="min-h-[200px] flex items-center justify-center">
                  <div className="text-xl text-[var(--color-text)] text-center">
                    {showAnswer ? currentCard.back : currentCard.front}
                  </div>
                </div>

                {!showAnswer ? (
                  <button
                    onClick={() => setShowAnswer(true)}
                    className="w-full py-3 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors"
                  >
                    Show Answer
                  </button>
                ) : (
                  <div className="space-y-2">
                    <div className="text-center text-sm text-[var(--color-text-muted)] mb-2">
                      How well did you know this?
                    </div>
                    <div className="grid grid-cols-4 gap-2">
                      {grades.map((grade) => (
                        <button
                          key={grade.value}
                          onClick={() => handleReview(grade.value)}
                          disabled={reviewCard.isPending}
                          className={`py-2 ${grade.color} text-white rounded hover:opacity-80 transition-opacity disabled:opacity-50`}
                        >
                          {grade.label}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="text-center text-[var(--color-text-muted)] py-12">
                <div className="text-4xl mb-4">🎉</div>
                <div className="text-xl">All caught up!</div>
                <div className="mt-2">No cards due for review right now.</div>
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
