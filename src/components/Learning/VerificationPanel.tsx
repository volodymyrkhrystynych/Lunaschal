import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type LearningCard, type VerifyResult } from '../../hooks/api';
import { MessageMarkdown } from '../MessageMarkdown';

interface Props {
  card: LearningCard;
  onClose: () => void;
  onRevised: () => void;
}

const VERDICT_STYLE: Record<string, string> = {
  supports: 'bg-green-500/20 text-green-400',
  contradicts: 'bg-red-500/20 text-red-400',
  partial: 'bg-orange-500/20 text-orange-400',
  notFound: 'bg-white/10 text-[var(--color-text-muted)]',
};

export function VerificationPanel({ card, onClose, onRevised }: Props) {
  const [result, setResult] = useState<VerifyResult | null>(null);
  const [followup, setFollowup] = useState('');
  const [editedAnswer, setEditedAnswer] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const verify = useMutation({
    mutationFn: () => api.learning.verify(card.id),
    onSuccess: setResult,
  });

  const askFollowup = useMutation({
    mutationFn: () =>
      api.learning.verifyFollowup(
        card.id,
        followup.trim(),
        result?.transcript ?? []
      ),
    onSuccess: r => {
      setResult(r);
      setFollowup('');
    },
  });

  const revise = useMutation({
    mutationFn: () =>
      api.learning.revise(card.id, {
        answer: (editedAnswer ?? result?.case?.proposedAnswer ?? '').trim(),
        triggerType: 'web_verification',
        sources: result?.case?.citations,
        note: result?.case?.summary,
      }),
    onSuccess: onRevised,
  });

  const generateFollowupCards = useMutation({
    mutationFn: () => {
      const c = result!.case!;
      const citationText = c.citations
        .map(x => `${x.title}: ${x.quote}`)
        .join('\n');
      return api.learning.generate({
        text: `${c.summary}\n\nEvidence:\n${citationText}`,
        folderId: card.folderId ?? undefined,
        derivedFrom: card.id,
        sourceType: 'verification',
      });
    },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['learning', 'queue'] }),
  });

  useEffect(() => {
    verify.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card.id]);

  const c = result?.case;
  const proposed = editedAnswer ?? c?.proposedAnswer ?? '';

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="bg-[var(--color-surface)] rounded-lg p-6 max-w-2xl w-full mx-4 max-h-[85vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-[var(--color-text)]">
            Verify against evidence
          </h2>
          <button
            onClick={onClose}
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            ✕
          </button>
        </div>

        <div className="border border-white/10 rounded-lg p-3 mb-4">
          <div className="text-sm text-[var(--color-text)]">
            <MessageMarkdown content={card.question} />
          </div>
          <div className="text-sm text-[var(--color-text-muted)] mt-1">
            <MessageMarkdown content={card.answer} />
          </div>
        </div>

        {verify.isPending && (
          <div className="text-center py-8 text-[var(--color-text-muted)]">
            <div className="animate-pulse">
              Consulting the folder's evidence provider…
            </div>
            <div className="text-xs mt-2">
              This can take a while — the agent is querying sources.
            </div>
          </div>
        )}

        {verify.isError && (
          <p className="text-sm text-red-400 py-4">
            {verify.error instanceof Error
              ? verify.error.message
              : 'Verification failed'}
          </p>
        )}

        {result?.status === 'noProvider' && (
          <p className="text-sm text-[var(--color-text-muted)] py-4">
            This card's folder has no evidence provider bound. Bind an MCP
            server to the folder (Learning → Folders) to verify against an
            authoritative source. Open-web search is deliberately not used.
          </p>
        )}

        {result?.status === 'providerUnsupported' && (
          <p className="text-sm text-[var(--color-text-muted)] py-4">
            {result.error ||
              'Verification requires the OpenAI or Ollama provider.'}
          </p>
        )}

        {result?.status === 'notFound' && (
          <p className="text-sm text-[var(--color-text-muted)] py-4">
            No authoritative source found. {c?.summary}
          </p>
        )}

        {result?.status === 'ok' && c && (
          <div className="space-y-4">
            <div>
              <span
                className={`px-2 py-0.5 text-xs rounded ${VERDICT_STYLE[c.verdict]}`}
              >
                {c.verdict === 'supports'
                  ? 'Supports the stored answer'
                  : c.verdict === 'contradicts'
                    ? 'Contradicts the stored answer'
                    : 'Partially supports the stored answer'}
              </span>
              <p className="text-sm text-[var(--color-text)] mt-3">
                {c.summary}
              </p>
            </div>

            {c.citations.length > 0 && (
              <div className="space-y-2">
                <div className="text-xs text-[var(--color-text-muted)] uppercase tracking-wide">
                  Citations
                </div>
                {c.citations.map((cite, i) => (
                  <blockquote
                    key={i}
                    className="border-l-2 border-[var(--color-primary)] pl-3 text-sm"
                  >
                    <div className="text-[var(--color-text)]">
                      "{cite.quote}"
                    </div>
                    <div className="text-xs text-[var(--color-text-muted)] mt-0.5">
                      {cite.title} — {cite.source}
                    </div>
                  </blockquote>
                ))}
              </div>
            )}

            {(c.verdict === 'contradicts' || c.verdict === 'partial') && (
              <div className="space-y-2">
                <div className="text-xs text-[var(--color-text-muted)] uppercase tracking-wide">
                  Proposed correction
                </div>
                <textarea
                  value={proposed}
                  onChange={e => setEditedAnswer(e.target.value)}
                  rows={3}
                  className="w-full bg-transparent text-sm text-[var(--color-text)] border border-white/10 rounded-lg p-3 resize-y focus:outline-none focus:border-[var(--color-primary)]"
                />
              </div>
            )}

            <div className="flex flex-wrap gap-2">
              {(c.verdict === 'contradicts' || c.verdict === 'partial') && (
                <button
                  onClick={() => revise.mutate()}
                  disabled={!proposed.trim() || revise.isPending}
                  className="px-4 py-2 text-sm bg-[var(--color-primary)] text-white rounded-lg hover:opacity-80 font-medium disabled:opacity-50"
                >
                  {revise.isPending
                    ? 'Updating…'
                    : 'Update answer (new version)'}
                </button>
              )}
              <button
                onClick={onClose}
                className="px-4 py-2 text-sm bg-white/10 text-[var(--color-text)] rounded-lg hover:bg-white/20"
              >
                Keep current answer
              </button>
              <button
                onClick={() => generateFollowupCards.mutate()}
                disabled={generateFollowupCards.isPending}
                className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
              >
                {generateFollowupCards.isPending
                  ? 'Generating…'
                  : generateFollowupCards.isSuccess
                    ? '✓ Cards queued'
                    : 'Make cards from this'}
              </button>
            </div>
          </div>
        )}

        {(result?.status === 'ok' || result?.status === 'notFound') && (
          <div className="mt-5 pt-4 border-t border-white/10 flex gap-2">
            <input
              value={followup}
              onChange={e => setFollowup(e.target.value)}
              onKeyDown={e => {
                if (
                  e.key === 'Enter' &&
                  followup.trim() &&
                  !askFollowup.isPending
                )
                  askFollowup.mutate();
              }}
              placeholder="Ask a follow-up against the same source…"
              className="flex-1 bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]"
            />
            <button
              onClick={() => askFollowup.mutate()}
              disabled={!followup.trim() || askFollowup.isPending}
              className="px-4 py-2 text-sm bg-white/10 text-[var(--color-text)] rounded-lg hover:bg-white/20 disabled:opacity-50"
            >
              {askFollowup.isPending ? 'Asking…' : 'Ask'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
