import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { readSSE } from '../../lib/sse';
import { useRecorder } from '../../hooks/useRecorder';
import { useShortcutScope } from '../../shortcuts/ShortcutProvider';
import { MessageMarkdown } from '../MessageMarkdown';
import { AgentSteps } from '../Chat/AgentSteps';
import { stepLabel, type AgentStep as ToolStep } from '../../lib/agentSteps';

interface IdeaDiscussionProps {
  ideaId: string;
}

/**
 * The idea's chat with the agent — its own tab, and as close to the Chat tab as
 * the backend allows: a scrolling transcript that owns the pane, a composer
 * pinned to the bottom, dictation that sends, and more than one thread per
 * idea.
 *
 * It was a section at the bottom of the detail scroll, which meant the composer
 * moved down the page as the answer streamed in and dictation was the one chat
 * in the app you couldn't talk to.
 */
export function IdeaDiscussion({ ideaId }: IdeaDiscussionProps) {
  const queryClient = useQueryClient();
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState('');
  const [liveSteps, setLiveSteps] = useState<ToolStep[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [threadId, setThreadId] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const { data: conversations } = useQuery({
    queryKey: ['ideas', ideaId, 'conversations'],
    queryFn: () => api.ideas.listConversations(ideaId),
  });
  // Newest first from the server; an explicit pick wins over it.
  const conversationId =
    (threadId && conversations?.some(c => c.id === threadId) ? threadId : '') ||
    conversations?.[0]?.id ||
    '';

  const { data: conversation } = useQuery({
    queryKey: ['chat', 'conversation', conversationId],
    queryFn: () => api.chat.getConversation(conversationId),
    enabled: !!conversationId,
  });

  const startConversation = useMutation({
    mutationFn: () => api.ideas.createConversation(ideaId),
    onSuccess: created => {
      setThreadId(created.id);
      queryClient.invalidateQueries({
        queryKey: ['ideas', ideaId, 'conversations'],
      });
    },
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

  const send = async (message?: string) => {
    const question = (message ?? input).trim();
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
      // The question is already persisted server-side; put it back in the box
      // only if nothing came back at all.
      setInput(prev => prev || question);
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

  // Dictation sends, same as the Chat tab and the Writing discussion: the
  // box-and-a-second-click step existed to catch a misheard proper noun, and
  // the two-model STT cross-check is what made it unnecessary. Anything
  // already typed goes with it as one message.
  const recorder = useRecorder(text => {
    const spoken = text.trim();
    if (!spoken) return;
    const typed = input.trim();
    void send(typed ? `${typed} ${spoken}` : spoken);
  });
  const recording = recorder.status === 'recording';
  const transcribing = recorder.status === 'transcribing';

  const messages = (conversation?.messages ?? []).filter(
    m => m.role !== 'system'
  );

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2 border-b border-white/10 shrink-0">
        <h3 className="text-sm font-medium text-[var(--color-text)]">
          Discuss with the agent
        </h3>
        {conversations && conversations.length > 1 && (
          <select
            value={conversationId}
            onChange={e => setThreadId(e.target.value)}
            aria-label="Thread"
            className="rounded bg-[var(--color-surface)] border border-white/10 px-1 py-0.5 text-xs text-[var(--color-text)] focus:outline-none"
          >
            {conversations.map((c, i) => (
              <option key={c.id} value={c.id}>
                {c.title || `Thread ${conversations.length - i}`}
              </option>
            ))}
          </select>
        )}
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => startConversation.mutate()}
          disabled={
            busy || startConversation.isPending || messages.length === 0
          }
          className="px-2 py-0.5 rounded text-xs bg-white/10 text-[var(--color-text)] hover:bg-white/15 disabled:opacity-40"
        >
          New thread
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {messages.length === 0 && !busy && (
          <p className="text-xs text-[var(--color-text-muted)]">
            Ask how others have solved this, or whether it fits what&apos;s
            already here. The agent can read the repository, search the web and
            consult its own research notes.
          </p>
        )}

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

      <div className="border-t border-white/10 p-3 shrink-0">
        {error && <p className="mb-2 text-xs text-red-400">{error}</p>}
        {recorder.error && (
          <p className="mb-2 text-xs text-red-400">{recorder.error}</p>
        )}
        <div className="flex gap-2">
          <textarea
            ref={inputRef}
            data-idea-discussion-input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            rows={2}
            placeholder="Ask about this idea… (Enter to send)"
            className="flex-1 resize-none rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1.5 text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
          />
          <button
            type="button"
            onClick={() => (recording ? recorder.stop() : recorder.start())}
            disabled={busy || transcribing || !recorder.canTranscribe}
            title={
              !recorder.canTranscribe
                ? 'Offline — dictation needs the server'
                : recording
                  ? 'Stop recording'
                  : 'Speak to send'
            }
            aria-label={recording ? 'Stop recording' : 'Speak to send'}
            className={`self-end px-3 py-2 rounded text-sm disabled:opacity-50 ${
              recording
                ? 'bg-red-500 text-white animate-pulse'
                : 'bg-white/5 border border-white/20 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
            }`}
          >
            {transcribing ? (
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="animate-spin"
              >
                <path d="M21 12a9 9 0 1 1-6.219-8.56" />
              </svg>
            ) : (
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="22" />
              </svg>
            )}
          </button>
          <button
            type="button"
            onClick={() => void send()}
            disabled={busy || !input.trim()}
            className="self-end px-3 py-2 rounded text-sm bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-40"
          >
            {busy ? '…' : 'Send'}
          </button>
        </div>
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
