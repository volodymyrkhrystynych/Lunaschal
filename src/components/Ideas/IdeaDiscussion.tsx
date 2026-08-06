import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { readSSE } from '../../lib/sse';
import { useShortcutScope } from '../../shortcuts/ShortcutProvider';
import { MessageMarkdown } from '../MessageMarkdown';
import { AgentSteps } from '../Chat/AgentSteps';
import { stepLabel, type AgentStep as ToolStep } from '../../lib/agentSteps';

interface IdeaDiscussionProps {
  ideaId: string;
}

export function IdeaDiscussion({ ideaId }: IdeaDiscussionProps) {
  const queryClient = useQueryClient();
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState('');
  const [liveSteps, setLiveSteps] = useState<ToolStep[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const { data: conversations } = useQuery({
    queryKey: ['ideas', ideaId, 'conversations'],
    queryFn: () => api.ideas.listConversations(ideaId),
  });
  const conversationId = conversations?.[0]?.id ?? '';

  const { data: conversation } = useQuery({
    queryKey: ['chat', 'conversation', conversationId],
    queryFn: () => api.chat.getConversation(conversationId),
    enabled: !!conversationId,
  });

  const startConversation = useMutation({
    mutationFn: () => api.ideas.createConversation(ideaId),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ['ideas', ideaId, 'conversations'],
      }),
  });

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [conversation?.messages?.length, streaming, liveSteps.length]);

  // Scope 3, the deepest in this view: list (1) → detail (2) → discussion (3).
  // Contiguous numbering is what lets `nav.in` reach it.
  useShortcutScope(3, {
    drillIn: () => {
      inputRef.current?.focus();
      return true;
    },
  });

  const send = async () => {
    const question = input.trim();
    if (!question || busy) return;

    let id = conversationId;
    if (!id) id = (await startConversation.mutateAsync()).id;

    setInput('');
    setBusy(true);
    setError('');
    setStreaming('');
    setLiveSteps([]);

    try {
      const response = await fetch(`/api/ideas/${ideaId}/discuss`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ conversationId: id, message: question }),
      });
      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');

      let answer = '';
      for await (const event of readSSE(reader)) {
        if (event.error) throw new Error(String(event.error));
        // Tool events arrive as each call finishes, so the minute of
        // gathering reads as progress rather than a spinner.
        if (event.tool) setLiveSteps(prev => [...prev, event as ToolStep]);
        if (event.content) {
          answer += String(event.content);
          setStreaming(answer);
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'The discussion failed');
    } finally {
      setBusy(false);
      setStreaming('');
      setLiveSteps([]);
      queryClient.invalidateQueries({ queryKey: ['chat', 'conversation', id] });
      queryClient.invalidateQueries({
        queryKey: ['ideas', ideaId, 'conversations'],
      });
    }
  };

  const messages = (conversation?.messages ?? []).filter(
    m => m.role !== 'system'
  );

  return (
    <div className="mx-4 my-4 border-t border-white/10 pt-4">
      <h3 className="text-sm font-medium text-[var(--color-text)] mb-2">
        Discuss with the agent
      </h3>

      {messages.length === 0 && !busy && (
        <p className="text-xs text-[var(--color-text-muted)] mb-2">
          Ask how others have solved this, or whether it fits what's already
          here. The agent can search the web and read its own research notes.
        </p>
      )}

      <div className="space-y-3">
        {messages.map(message => {
          const meta = message.metadata ? safeParse(message.metadata) : null;
          return (
            <div key={message.id}>
              <div
                className={
                  message.role === 'user'
                    ? 'text-sm text-[var(--color-text)] whitespace-pre-wrap bg-white/5 rounded p-2'
                    : 'text-sm text-[var(--color-text)]'
                }
              >
                {message.role === 'user' ? (
                  message.content
                ) : (
                  <MessageMarkdown content={message.content} />
                )}
              </div>
              <AgentSteps steps={meta?.steps ?? []} />
              {meta?.sources?.length ? (
                <ul className="mt-1 text-xs">
                  {meta.sources.map(
                    (source: { url: string; title?: string }) => (
                      <li key={source.url}>
                        <a
                          href={source.url}
                          target="_blank"
                          rel="noreferrer noopener"
                          className="text-[var(--color-primary)] hover:underline"
                        >
                          {source.title || source.url}
                        </a>
                      </li>
                    )
                  )}
                </ul>
              ) : null}
            </div>
          );
        })}

        {busy && (
          <div className="text-xs text-[var(--color-text-muted)]">
            {liveSteps.length === 0 && !streaming ? (
              <p>Working…</p>
            ) : (
              <ul className="space-y-0.5">
                {liveSteps.map((step, i) => (
                  <li key={i}>· {stepLabel(step)}</li>
                ))}
              </ul>
            )}
          </div>
        )}
        {streaming && (
          <div className="text-sm text-[var(--color-text)]">
            <MessageMarkdown content={streaming} />
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {error && <p className="mt-2 text-xs text-red-400">{error}</p>}

      <div className="flex gap-2 mt-3">
        <textarea
          ref={inputRef}
          data-idea-discussion-input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          rows={2}
          placeholder="Ask about this idea…"
          className="flex-1 resize-none rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1.5 text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
        />
        <button
          type="button"
          onClick={send}
          disabled={busy || !input.trim()}
          className="px-3 py-1 rounded text-sm bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-40 self-end"
        >
          {busy ? '…' : 'Send'}
        </button>
      </div>
    </div>
  );
}

function safeParse(
  raw: string
): { steps?: ToolStep[]; sources?: { url: string }[] } | null {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}
