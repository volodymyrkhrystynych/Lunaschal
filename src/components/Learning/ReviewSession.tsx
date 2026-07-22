import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type GradeResult, type LearningCard } from '../../hooks/api';
import { ulid } from '../../lib/ulid';
import { useLearningReview } from '../../offline/mutationDefaults';
import { useRecorder } from '../../hooks/useRecorder';
import {
  useShortcutScope,
  useShortcuts,
} from '../../shortcuts/ShortcutProvider';
import {
  LEARNING_FONT_SIZE_STEP,
  getStoredLearningFontSize,
  setStoredLearningFontSize,
} from '../../lib/fontSize';
import { MessageMarkdown } from '../MessageMarkdown';
import { CardChat } from './CardChat';
import { CoverageResult } from './CoverageResult';
import { VerificationPanel } from './VerificationPanel';

interface Props {
  folderId: string | null;
  tag: string | null;
}

const RATINGS = [
  { value: 1, label: 'Again', color: 'bg-red-500' },
  { value: 2, label: 'Hard', color: 'bg-orange-500' },
  { value: 3, label: 'Good', color: 'bg-yellow-500' },
  { value: 4, label: 'Easy', color: 'bg-green-500' },
];

// One finished card from the answering pass. 'answered' cards were sent to the
// grader in the background; 'skipped' cards were flipped past and get
// self-rated when the results pass reveals their answer.
interface Attempt {
  card: LearningCard;
  mode: 'answered' | 'skipped';
  answer?: string;
  usedVoice?: boolean;
}

type GradeState = GradeResult | 'pending' | 'error';

export function ReviewSession({ folderId, tag }: Props) {
  // Cards are snapshotted per session so background refetches can't reorder
  // or shrink the deck mid-pass.
  const [sessionCards, setSessionCards] = useState<LearningCard[] | null>(null);
  const [index, setIndex] = useState(0);
  const [answer, setAnswer] = useState('');
  const [usedVoice, setUsedVoice] = useState(false);
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const [grades, setGrades] = useState<Record<string, GradeState>>({});
  const [resultIndex, setResultIndex] = useState(0);
  const [ratingOverride, setRatingOverride] = useState<number | null>(null);
  const [verifying, setVerifying] = useState<LearningCard | null>(null);
  const [showChat, setShowChat] = useState(false);
  const [fontSize, setFontSize] = useState(getStoredLearningFontSize);
  const { setLevel } = useShortcuts();
  const queryClient = useQueryClient();

  const { data: due, refetch: refetchDue } = useQuery({
    queryKey: ['learning', 'due', folderId, tag],
    queryFn: () =>
      api.learning.getDue({
        folderId: folderId ?? undefined,
        tag: tag ?? undefined,
      }),
  });

  useEffect(() => {
    if (!due) return;
    setSessionCards(prev =>
      prev === null || (prev.length === 0 && due.length > 0) ? due : prev
    );
  }, [due]);

  const total = sessionCards?.length ?? 0;

  // The answer box auto-focuses the moment a card appears; match that by
  // drilling the shortcut depth straight to the card level (2) so nav keys act
  // on the card instead of cycling app tabs. One-shot per mount: it fires once
  // the deck is ready (after the view-entry reset to level 0), and never
  // re-asserts, so the user can still Escape/A back out.
  const autoDrilledRef = useRef(false);
  useEffect(() => {
    if (!autoDrilledRef.current && total > 0) {
      autoDrilledRef.current = true;
      setLevel(2);
    }
  }, [total, setLevel]);
  const phase: 'answer' | 'results' =
    total > 0 && attempts.length >= total ? 'results' : 'answer';
  const card = sessionCards?.[index];

  const current = phase === 'results' ? attempts[resultIndex] : undefined;
  const currentGrade = current ? grades[current.card.id] : undefined;
  const resolvedGrade =
    currentGrade && currentGrade !== 'pending' && currentGrade !== 'error'
      ? currentGrade
      : null;

  // The grader's suggestion is pre-selected the instant it renders (derived,
  // not effect-synced, so a fast Space press can't commit a stale default).
  const selRating = ratingOverride ?? resolvedGrade?.suggestedRating ?? 3;

  const recorder = useRecorder(text => {
    setAnswer(prev => (prev ? `${prev} ${text}` : text));
    setUsedVoice(true);
  });

  const adjustFontSize = (delta: number) => {
    setFontSize(px => setStoredLearningFontSize(px + delta));
  };

  // Fire grading in the background and move straight to the next card.
  const submitAnswer = () => {
    if (!card || !answer.trim() || recorder.status !== 'idle') return;
    const cardId = card.id;
    setGrades(g => ({ ...g, [cardId]: 'pending' }));
    api.learning
      .grade(cardId, { answer, answerMode: usedVoice ? 'voice' : 'typed' })
      .then(res => setGrades(g => ({ ...g, [cardId]: res })))
      .catch(() => setGrades(g => ({ ...g, [cardId]: 'error' })));
    setAttempts(a => [...a, { card, mode: 'answered', answer, usedVoice }]);
    setAnswer('');
    setUsedVoice(false);
    setIndex(i => i + 1);
  };

  // Flip = skip for now; the answer is revealed in the results pass.
  const skipCard = () => {
    if (!card) return;
    setAttempts(a => [...a, { card, mode: 'skipped' }]);
    setAnswer('');
    setUsedVoice(false);
    setIndex(i => i + 1);
  };

  const finishSession = async () => {
    const { data: fresh } = await refetchDue();
    setSessionCards(fresh ?? []);
    setAttempts([]);
    setGrades({});
    setIndex(0);
    setResultIndex(0);
    setShowChat(false);
  };

  const advanceResult = () => {
    setShowChat(false);
    setRatingOverride(null);
    if (resultIndex + 1 >= attempts.length) void finishSession();
    else setResultIndex(i => i + 1);
  };

  // Offline-queueable. Reviews advance FSRS and so aren't naturally
  // idempotent: each carries a client `reviewId` the server dedupes on. Stats
  // invalidation lives in the registered defaults.
  const review = useLearningReview();
  // A paused (offline-queued) mutation stays `isPending`, so gate the UI on
  // "actively in flight" — otherwise offline you couldn't rate the next card.
  const reviewBusy = review.isPending && !review.isPaused;

  // Advance the results pass optimistically rather than in the mutation's
  // onSuccess — offline the mutation is paused, so waiting on it would stall
  // the session. The rating is safely queued and replays on reconnect.
  const submitRating = (rating: number) => {
    const a = attempts[resultIndex];
    if (!a) return;
    const g = grades[a.card.id];
    const resolved = g && g !== 'pending' && g !== 'error' ? g : null;
    review.mutate({
      cardId: a.card.id,
      reviewId: ulid(),
      rating,
      suggestedRating: resolved?.suggestedRating,
      userAnswer: resolved ? resolved.normalizedAnswer : a.answer,
      coverage: resolved?.coverage,
      answerMode:
        a.mode === 'skipped' ? 'self' : a.usedVoice ? 'voice' : 'typed',
    });
    advanceResult();
  };

  useShortcutScope(2, {
    // Move the highlighted rating during the results pass.
    next: () => {
      if (phase === 'results') setRatingOverride(Math.min(selRating + 1, 4));
    },
    prev: () => {
      if (phase === 'results') setRatingOverride(Math.max(selRating - 1, 1));
    },
    record: () => {
      if (phase !== 'answer' || !card) return;
      if (recorder.status === 'recording') recorder.stop();
      else if (recorder.status === 'idle') recorder.start();
    },
    check: () => {
      if (phase === 'answer') submitAnswer();
    },
    // Space skips the current card in the answering pass; in the results pass
    // it commits the highlighted rating. D is left alone — it must never
    // touch card state.
    flip: () => {
      if (phase === 'answer') {
        skipCard();
      } else if (current && !reviewBusy) {
        submitRating(selRating);
      }
    },
    rate: rating => {
      if (phase !== 'results' || !current || reviewBusy) return;
      submitRating(rating);
    },
    fontUp: () => adjustFontSize(LEARNING_FONT_SIZE_STEP),
    fontDown: () => adjustFontSize(-LEARNING_FONT_SIZE_STEP),
  });

  if (total === 0) {
    return (
      <div className="text-center py-16">
        <div className="text-6xl mb-4">🎉</div>
        <div className="text-2xl font-semibold text-[var(--color-text)] mb-2">
          All caught up!
        </div>
        <div className="text-[var(--color-text-muted)]">
          No cards due for review right now.
        </div>
      </div>
    );
  }

  if (phase === 'answer' && card) {
    return (
      <div className="max-w-xl mx-auto">
        <div className="bg-[var(--color-surface)] rounded-lg border border-white/10 overflow-hidden">
          <div className="h-1 bg-white/5">
            <div
              className="h-full bg-[var(--color-primary)] transition-all duration-300"
              style={{ width: `${((index + 1) / total) * 100}%` }}
            />
          </div>
          <div className="p-8">
            <div className="text-center mb-6 text-sm text-[var(--color-text-muted)]">
              Card {index + 1} of {total}
            </div>
            <div
              className="text-[var(--color-text)] text-center leading-relaxed mb-6"
              style={{ fontSize: `${fontSize}px` }}
            >
              <MessageMarkdown content={card.question} />
            </div>

            <div className="space-y-3">
              <textarea
                value={answer}
                onChange={e => setAnswer(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !e.shiftKey && answer.trim()) {
                    e.preventDefault();
                    submitAnswer();
                  }
                }}
                placeholder="Type your answer (or record it)…"
                rows={3}
                autoFocus
                className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg p-3 resize-none focus:outline-none focus:border-[var(--color-primary)]"
              />
              {recorder.error && (
                <p className="text-xs text-red-400">{recorder.error}</p>
              )}
              <div className="flex gap-2">
                <button
                  onClick={submitAnswer}
                  disabled={!answer.trim() || recorder.status !== 'idle'}
                  className="flex-1 py-3 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors font-medium disabled:opacity-50"
                >
                  Check Answer
                </button>
                <button
                  onClick={() =>
                    recorder.status === 'recording'
                      ? recorder.stop()
                      : recorder.start()
                  }
                  disabled={recorder.status === 'transcribing'}
                  className={`px-4 py-3 rounded-lg font-medium transition-colors disabled:opacity-50 ${
                    recorder.status === 'recording'
                      ? 'bg-red-600 hover:bg-red-700 text-white'
                      : 'bg-white/10 hover:bg-white/20 text-[var(--color-text)]'
                  }`}
                >
                  {recorder.status === 'recording'
                    ? '■ Stop'
                    : recorder.status === 'transcribing'
                      ? '…'
                      : '🎤'}
                </button>
                <button
                  onClick={skipCard}
                  className="px-4 py-3 rounded-lg bg-white/10 hover:bg-white/20 text-[var(--color-text)] font-medium transition-colors"
                  title="Skip for now — the answer is revealed at the end of the session"
                >
                  Flip
                </button>
              </div>
              <p className="text-center text-xs text-[var(--color-text-muted)]">
                Answers are checked in the background — results after the last
                card.
              </p>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (!current) return null;

  return (
    <div className="max-w-xl mx-auto">
      <div className="bg-[var(--color-surface)] rounded-lg border border-white/10 overflow-hidden">
        <div className="h-1 bg-white/5">
          <div
            className="h-full bg-green-500 transition-all duration-300"
            style={{ width: `${((resultIndex + 1) / attempts.length) * 100}%` }}
          />
        </div>
        <div className="p-8">
          <div className="text-center mb-6 text-sm text-[var(--color-text-muted)]">
            Result {resultIndex + 1} of {attempts.length}
          </div>
          <div
            className="text-[var(--color-text)] text-center leading-relaxed mb-6"
            style={{ fontSize: `${fontSize}px` }}
          >
            <MessageMarkdown content={current.card.question} />
          </div>

          <div className="space-y-4">
            <div className="bg-white/5 rounded-lg p-4">
              <div className="text-xs text-[var(--color-text-muted)] mb-1 uppercase tracking-wide">
                Answer
              </div>
              <div
                className="text-[var(--color-text)]"
                style={{ fontSize: `${fontSize}px` }}
              >
                <MessageMarkdown content={current.card.answer} />
              </div>
            </div>

            {current.mode === 'answered' && (
              <div className="bg-white/5 rounded-lg p-4">
                <div className="text-xs text-[var(--color-text-muted)] mb-1 uppercase tracking-wide">
                  Your answer
                </div>
                <div className="text-[var(--color-text)]">{current.answer}</div>
              </div>
            )}

            {currentGrade === 'pending' && (
              <div className="text-center text-sm text-[var(--color-text-muted)]">
                Checking your answer…
              </div>
            )}
            {currentGrade === 'error' && (
              <div className="text-center text-sm text-red-400">
                Automatic grading failed — rate yourself.
              </div>
            )}
            {resolvedGrade && (
              <CoverageResult
                coverage={resolvedGrade.coverage}
                normalizedAnswer={
                  current.usedVoice ? resolvedGrade.normalizedAnswer : undefined
                }
              />
            )}

            <div className="text-center text-sm text-[var(--color-text-muted)]">
              {resolvedGrade
                ? 'How hard was it to recall? (suggestion highlighted)'
                : 'How well did you know this?'}
            </div>
            <div className="grid grid-cols-4 gap-2">
              {RATINGS.map(r => (
                <button
                  key={r.value}
                  onClick={() => submitRating(r.value)}
                  disabled={reviewBusy}
                  className={`py-3 ${r.color} text-white rounded-lg hover:opacity-80 transition-all disabled:opacity-50 font-medium ${
                    selRating === r.value ? 'ring-2 ring-white' : 'opacity-70'
                  }`}
                >
                  {r.label}
                </button>
              ))}
            </div>

            <div className="flex justify-center gap-4">
              {!showChat && (
                <button
                  onClick={() => setShowChat(true)}
                  className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] underline"
                >
                  💬 Discuss this card
                </button>
              )}
              {resolvedGrade && (
                <button
                  onClick={() => setVerifying(current.card)}
                  className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] underline"
                >
                  I was right — the card is wrong
                </button>
              )}
            </div>

            {showChat && (
              <CardChat
                key={current.card.id}
                card={current.card}
                userAnswer={resolvedGrade?.normalizedAnswer ?? current.answer}
              />
            )}
          </div>
        </div>
      </div>

      {verifying && (
        <VerificationPanel
          card={verifying}
          onClose={() => setVerifying(null)}
          onRevised={() => {
            // The card was retired by the revision — skip rating it.
            setVerifying(null);
            queryClient.invalidateQueries({ queryKey: ['learning'] });
            advanceResult();
          }}
        />
      )}
    </div>
  );
}
