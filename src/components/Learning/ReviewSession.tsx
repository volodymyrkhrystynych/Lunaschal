import { useState, type RefObject } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type GradeResult, type LearningCard } from '../../hooks/api';
import { useRecorder } from '../../hooks/useRecorder';
import { useShortcutScope } from '../../shortcuts/ShortcutProvider';
import { LEARNING_CARD_FONT_SIZE_DEFAULT } from '../../lib/fontSize';
import { playCompletionChime } from '../../lib/sound';
import { MessageMarkdown } from '../MessageMarkdown';
import { CoverageResult, COVERAGE_DEFAULT_FONT_SIZE } from './CoverageResult';
import { VerificationPanel } from './VerificationPanel';

interface Props {
  folderId: string | null;
  tag: string | null;
  scrollRef?: RefObject<HTMLDivElement | null>;
  fontSize?: number;
}

const RATINGS = [
  { value: 1, label: 'Again', color: 'bg-red-500' },
  { value: 2, label: 'Hard', color: 'bg-orange-500' },
  { value: 3, label: 'Good', color: 'bg-yellow-500' },
  { value: 4, label: 'Easy', color: 'bg-green-500' },
];

// Matches Tailwind's max-w-xl at the default card font size — grows
// proportionally as the card is zoomed in, but never past the available
// width, so the card widens before its code blocks resort to scrolling.
const CARD_BASE_MAX_WIDTH_PX = 576;

export function ReviewSession({ folderId, tag, scrollRef, fontSize }: Props) {
  const [index, setIndex] = useState(0);
  const [answer, setAnswer] = useState('');
  const [usedVoice, setUsedVoice] = useState(false);
  const [flipped, setFlipped] = useState(false);
  const [grade, setGrade] = useState<GradeResult | null>(null);
  const [selRating, setSelRating] = useState(3);
  const [verifying, setVerifying] = useState<LearningCard | null>(null);
  const queryClient = useQueryClient();

  const { data: due, refetch: refetchDue } = useQuery({
    queryKey: ['learning', 'due', folderId, tag],
    queryFn: () =>
      api.learning.getDue({
        folderId: folderId ?? undefined,
        tag: tag ?? undefined,
      }),
  });

  const card = due?.[index];

  const recorder = useRecorder(text => {
    setAnswer(prev => (prev ? `${prev} ${text}` : text));
    setUsedVoice(true);
  });

  const gradeAnswer = useMutation({
    mutationFn: () =>
      api.learning.grade(card!.id, {
        answer,
        answerMode: usedVoice ? 'voice' : 'typed',
      }),
    onSuccess: g => {
      setGrade(g);
      setSelRating(g.suggestedRating);
      playCompletionChime();
    },
  });

  const review = useMutation({
    mutationFn: (rating: number) =>
      api.learning.review(card!.id, {
        rating,
        suggestedRating: grade?.suggestedRating,
        userAnswer: grade ? grade.normalizedAnswer : undefined,
        coverage: grade?.coverage,
        answerMode: grade ? (usedVoice ? 'voice' : 'typed') : 'self',
      }),
    onSuccess: async () => {
      queryClient.invalidateQueries({ queryKey: ['learning', 'stats'] });
      const { data: freshDue } = await refetchDue();
      setAnswer('');
      setUsedVoice(false);
      setFlipped(false);
      setGrade(null);
      setSelRating(3);
      setIndex(prev => (freshDue && prev < freshDue.length ? prev : 0));
    },
  });

  const answered = grade !== null || flipped;

  useShortcutScope(2, {
    // No next/prev here — rating is chosen with 1-4, so W/S scroll instead.
    scrollDown: () =>
      scrollRef?.current?.scrollBy({ top: 120, behavior: 'smooth' }),
    scrollUp: () =>
      scrollRef?.current?.scrollBy({ top: -120, behavior: 'smooth' }),
    // No drillIn here — Space flips and 1-4 rate, so D is left for navigation
    // only and does nothing extra once already on the card.
    record: () => {
      if (!card || answered || gradeAnswer.isPending) return;
      if (recorder.status === 'recording') recorder.stop();
      else if (recorder.status === 'idle') recorder.start();
    },
    check: () => {
      if (!card || answered || gradeAnswer.isPending) return;
      if (answer.trim() && recorder.status === 'idle') gradeAnswer.mutate();
    },
    flip: () => {
      if (!card || answered || gradeAnswer.isPending) return;
      setFlipped(true);
    },
    rate: rating => {
      if (!card || !answered || review.isPending) return;
      review.mutate(rating);
    },
  });

  if (!card) {
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

  const effectiveFontSize = fontSize ?? LEARNING_CARD_FONT_SIZE_DEFAULT;
  const zoomRatio = effectiveFontSize / LEARNING_CARD_FONT_SIZE_DEFAULT;
  // Rounded to avoid floating-point noise like 604.8000000000001 in styles.
  const cardMaxWidthPx =
    Math.round(CARD_BASE_MAX_WIDTH_PX * zoomRatio * 10) / 10;
  const assessmentFontSize =
    Math.round(COVERAGE_DEFAULT_FONT_SIZE * zoomRatio * 100) / 100;

  return (
    <div
      className="mx-auto"
      style={{ maxWidth: `min(${cardMaxWidthPx}px, 100%)` }}
    >
      <div className="bg-[var(--color-surface)] rounded-lg border border-white/10 overflow-hidden">
        <div className="h-1 bg-white/5">
          <div
            className="h-full bg-[var(--color-primary)] transition-all duration-300"
            style={{ width: `${((index + 1) / (due?.length || 1)) * 100}%` }}
          />
        </div>
        <div className="p-8">
          <div className="text-center mb-6 text-sm text-[var(--color-text-muted)]">
            Card {index + 1} of {due?.length}
          </div>
          <div
            className="text-[var(--color-text)] text-center leading-relaxed mb-6"
            style={{ fontSize: `${effectiveFontSize}px` }}
          >
            <MessageMarkdown content={card.question} />
          </div>

          {!answered && (
            <div className="space-y-3">
              <textarea
                value={answer}
                onChange={e => setAnswer(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !e.shiftKey && answer.trim()) {
                    e.preventDefault();
                    gradeAnswer.mutate();
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
                  onClick={() => gradeAnswer.mutate()}
                  disabled={
                    !answer.trim() ||
                    gradeAnswer.isPending ||
                    recorder.status !== 'idle'
                  }
                  className="flex-1 py-3 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors font-medium disabled:opacity-50"
                >
                  {gradeAnswer.isPending ? 'Checking…' : 'Check Answer'}
                </button>
                <button
                  onClick={() =>
                    recorder.status === 'recording'
                      ? recorder.stop()
                      : recorder.start()
                  }
                  disabled={
                    recorder.status === 'transcribing' || gradeAnswer.isPending
                  }
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
                  onClick={() => setFlipped(true)}
                  disabled={gradeAnswer.isPending}
                  className="px-4 py-3 rounded-lg bg-white/10 hover:bg-white/20 text-[var(--color-text)] font-medium transition-colors disabled:opacity-50"
                  title="Just show the answer and grade yourself"
                >
                  Flip
                </button>
              </div>
              {gradeAnswer.isError && (
                <p className="text-xs text-red-400">
                  {gradeAnswer.error instanceof Error
                    ? gradeAnswer.error.message
                    : 'Grading failed'}
                </p>
              )}
            </div>
          )}

          {answered && (
            <div className="space-y-4">
              <div className="bg-white/5 rounded-lg p-4">
                <div className="text-xs text-[var(--color-text-muted)] mb-1 uppercase tracking-wide">
                  Answer
                </div>
                <div
                  className="text-[var(--color-text)]"
                  style={{ fontSize: `${effectiveFontSize}px` }}
                >
                  <MessageMarkdown content={card.answer} />
                </div>
              </div>

              {grade && (
                <CoverageResult
                  coverage={grade.coverage}
                  normalizedAnswer={
                    usedVoice ? grade.normalizedAnswer : undefined
                  }
                  fontSize={assessmentFontSize}
                />
              )}

              <div className="text-center text-sm text-[var(--color-text-muted)]">
                {grade
                  ? 'How hard was it to recall? (suggestion highlighted)'
                  : 'How well did you know this?'}
              </div>
              <div className="grid grid-cols-4 gap-2">
                {RATINGS.map(r => (
                  <button
                    key={r.value}
                    onClick={() => review.mutate(r.value)}
                    disabled={review.isPending}
                    className={`py-3 ${r.color} text-white rounded-lg hover:opacity-80 transition-all disabled:opacity-50 font-medium ${
                      selRating === r.value ? 'ring-2 ring-white' : 'opacity-70'
                    }`}
                  >
                    {r.label}
                  </button>
                ))}
              </div>

              {grade && (
                <div className="text-center">
                  <button
                    onClick={() => setVerifying(card)}
                    className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] underline"
                  >
                    I was right — the card is wrong
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {verifying && (
        <VerificationPanel
          card={verifying}
          onClose={() => setVerifying(null)}
          onRevised={() => {
            setVerifying(null);
            setAnswer('');
            setUsedVoice(false);
            setFlipped(false);
            setGrade(null);
            queryClient.invalidateQueries({ queryKey: ['learning'] });
          }}
        />
      )}
    </div>
  );
}
