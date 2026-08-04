import { useState, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type ChatMode, type DraftCard } from '../../hooks/api';
import { MessageMarkdown } from '../MessageMarkdown';
import { BriefingTodos } from '../BriefingTodos';
import {
  contextMessages,
  isBreak,
  parseProposedTodos,
} from '@/lib/chatSegments';
import { readSSE } from '@/lib/sse';
import { formatMessageTime } from '@/lib/chatTime';
import {
  parseWebSearchMeta,
  stepLabel,
  type WebSearchStep,
} from '@/lib/websearchSteps';

interface PendingSave {
  messageId: string;
  data: {
    title: string;
    description?: string;
    date?: string;
    time?: string;
    tags: string[];
  };
}

interface PendingQuiz {
  topic: string;
  messageId: string;
}

interface ClassifyResult {
  intent:
    | 'calendar'
    | 'flashcard_request'
    | 'note_to_self'
    | 'question'
    | 'conversation';
  confidence: number;
  calendarEvent?: {
    title: string;
    description?: string;
    date?: string;
    time?: string;
    tags: string[];
  };
  flashcardRequest?: { topic: string };
  noteToSelf?: { content: string };
}

const BREAK_METADATA = JSON.stringify({ break: true });

interface ChatPanelProps {
  mode: ChatMode;
}

export function ChatPanel({ mode }: ChatPanelProps) {
  const [input, setInput] = useState('');
  const [streamingContent, setStreamingContent] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [liveSteps, setLiveSteps] = useState<WebSearchStep[]>([]);
  const [pendingSave, setPendingSave] = useState<PendingSave | null>(null);
  const [pendingQuiz, setPendingQuiz] = useState<PendingQuiz | null>(null);
  const [queuedCards, setQueuedCards] = useState<number | null>(null);
  const [noteCards, setNoteCards] = useState<DraftCard[] | null>(null);
  // Which drafted note card has its "request changes" text box open.
  const [noteRegenId, setNoteRegenId] = useState<string | null>(null);
  const [noteRegenDirection, setNoteRegenDirection] = useState('');
  const [noteDupHint, setNoteDupHint] = useState<{
    cardId: string;
    similar: { question: string; answer: string };
    score: number;
  } | null>(null);
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const lastBreakRef = useRef<HTMLDivElement>(null);
  // When set, the next scroll effect pins the newest break divider to the top
  // (the "New chat" clear) instead of scrolling to the bottom.
  const justBrokeRef = useRef(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const queryClient = useQueryClient();
  const isWebSearch = mode === 'websearch';

  // The single conversation for the current chat day (4am -> 4am), one per mode.
  const { data: conversation } = useQuery({
    queryKey: ['chat', 'today', mode],
    queryFn: () => api.chat.today(mode),
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });

  const invalidateToday = () =>
    queryClient.invalidateQueries({ queryKey: ['chat', 'today', mode] });

  const createConversation = useMutation({
    mutationFn: () => api.chat.createConversation({ mode }),
    onSuccess: invalidateToday,
  });

  const addMessage = useMutation({
    mutationFn: ({
      convId,
      role,
      content,
      metadata,
    }: {
      convId: string;
      role: string;
      content: string;
      metadata?: string;
    }) => api.chat.addMessage(convId, { role, content, metadata }),
    onSuccess: invalidateToday,
  });

  const classifyMessage = useMutation({
    mutationFn: (message: string) => api.chat.classify(message),
  });

  /** Ask the model whether the message was really a calendar event, a
   * flashcard request or a note to self, and offer to save it if so.
   * Fire-and-forget: a failed classification just means no offer. Only for
   * the regular chat — a web-search lookup ("what's the weather in Tokyo")
   * isn't calendar/note material and the offer would just be noise. */
  const classifyUserMessage = (message: string, messageId: string) => {
    classifyMessage.mutate(message, {
      onSuccess: result => {
        const r = result as ClassifyResult;
        if (r.confidence < 0.7) return;
        if (r.intent === 'calendar' && r.calendarEvent) {
          setPendingSave({
            messageId,
            data: {
              title: r.calendarEvent.title,
              description: r.calendarEvent.description,
              date: r.calendarEvent.date,
              time: r.calendarEvent.time,
              tags: r.calendarEvent.tags,
            },
          });
        } else if (r.intent === 'flashcard_request' && r.flashcardRequest) {
          setPendingQuiz({ topic: r.flashcardRequest.topic, messageId });
        } else if (r.intent === 'note_to_self' && r.noteToSelf?.content) {
          generateFromNote.mutate(r.noteToSelf.content);
        }
      },
    });
  };

  const saveCalendar = useMutation({
    mutationFn: api.chat.saveCalendar,
    onSuccess: () => {
      invalidateToday();
      queryClient.invalidateQueries({ queryKey: ['calendar'] });
      setPendingSave(null);
    },
  });

  const generateForTopic = useMutation({
    mutationFn: (topic: string) => api.learning.generateForTopic(topic),
    onSuccess: result => {
      setQueuedCards(result.count);
      setPendingQuiz(null);
      queryClient.invalidateQueries({ queryKey: ['learning'] });
      setTimeout(() => setQueuedCards(null), 8000);
    },
  });

  /** "note to self" drafts a lesson card right away — the preview appears
   * inline below so it can be approved or steered without leaving chat. */
  const generateFromNote = useMutation({
    mutationFn: (content: string) => api.learning.generateFromNote(content),
    onSuccess: result =>
      setNoteCards(cards => (cards ?? []).concat(result.cards)),
  });

  const approveNoteCard = useMutation({
    mutationFn: ({ id, force }: { id: string; force?: boolean }) =>
      api.learning.approve(id, force),
    onSuccess: (result, { id }) => {
      if (result.status === 'duplicateHint' && result.similar) {
        setNoteDupHint({
          cardId: id,
          similar: result.similar,
          score: result.score ?? 0,
        });
        return;
      }
      setNoteDupHint(null);
      setNoteCards(cards => (cards ?? []).filter(c => c.id !== id));
      queryClient.invalidateQueries({ queryKey: ['learning'] });
    },
  });

  const regenerateNoteCard = useMutation({
    mutationFn: ({ id, direction }: { id: string; direction: string }) =>
      api.learning.regenerate(id, direction),
    onSuccess: (result, { id }) => {
      setNoteCards(cards =>
        (cards ?? []).filter(c => c.id !== id).concat(result.cards ?? [])
      );
      setNoteRegenId(null);
      setNoteRegenDirection('');
    },
  });

  const discardNoteCard = useMutation({
    mutationFn: (id: string) => api.learning.deny(id),
    onSuccess: (_result, id) => {
      setNoteCards(cards => (cards ?? []).filter(c => c.id !== id));
      setNoteDupHint(hint => (hint?.cardId === id ? null : hint));
    },
  });

  const messages = conversation?.messages || [];
  const conversationId = conversation?.id ?? null;
  const hasBreaks = messages.some(isBreak);
  // Only real user/assistant turns count toward "is there anything to clear".
  const hasChat = messages.some(m => m.role !== 'system');
  // The id of the last break marker, so only that divider carries the ref.
  const lastBreakId = [...messages].reverse().find(isBreak)?.id ?? null;

  useEffect(() => {
    if (justBrokeRef.current) {
      justBrokeRef.current = false;
      const c = scrollContainerRef.current;
      const b = lastBreakRef.current;
      if (c && b) {
        const cRect = c.getBoundingClientRect();
        const bRect = b.getBoundingClientRect();
        c.scrollTop += bRect.top - cRect.top - 8;
      }
      return;
    }
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [
    messages,
    streamingContent,
    liveSteps,
    pendingSave,
    pendingQuiz,
    queuedCards,
    noteCards,
  ]);

  const startNewChat = async () => {
    if (!conversationId || !hasChat || isStreaming) return;
    justBrokeRef.current = true;
    await addMessage.mutateAsync({
      convId: conversationId,
      role: 'system',
      content: '',
      metadata: BREAK_METADATA,
    });
  };

  const sendMessage = async (messageText?: string) => {
    const userMessage = (messageText ?? input).trim();
    if (!userMessage || isStreaming) return;

    if (messageText === undefined) setInput('');

    let convId = conversationId;

    if (!convId) {
      const result = await createConversation.mutateAsync();
      convId = result.id;
    }

    const userMsgResult = await addMessage.mutateAsync({
      convId,
      role: 'user',
      content: userMessage,
    });

    // Only the current segment (since the last "New chat") is sent to the model,
    // so the button acts as a true clear while history stays visible/saved.
    // createdAt rides along so the backend can prefix each turn with when it
    // was sent — the model is otherwise blind to gaps in the conversation.
    const chatMessages = [
      ...contextMessages(messages).map(m => ({
        role: m.role,
        content: m.content,
        createdAt: m.createdAt,
      })),
      {
        role: 'user' as const,
        content: userMessage,
        createdAt: new Date().toISOString(),
      },
    ];

    setIsStreaming(true);
    setStreamingContent('');
    setLiveSteps([]);

    let fullContent = '';
    try {
      const response = await fetch(
        isWebSearch ? '/api/chat/websearch/stream' : '/api/chat/stream',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ messages: chatMessages }),
        }
      );

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || 'Failed to get response');
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');

      let steps: WebSearchStep[] = [];
      let sources: { url: string; title?: string }[] = [];
      for await (const parsed of readSSE(reader)) {
        // Tool events arrive as each call finishes, so the gathering pass
        // reads as progress rather than a spinner.
        if (parsed.tool) {
          steps = [...steps, parsed as WebSearchStep];
          setLiveSteps(steps);
        }
        if (parsed.content) {
          fullContent += parsed.content;
          setStreamingContent(fullContent);
        }
        if (parsed.done && Array.isArray(parsed.sources)) {
          sources = parsed.sources as { url: string; title?: string }[];
        }
        if (parsed.error) throw new Error(parsed.error);
      }

      const metadata =
        isWebSearch && (steps.length > 0 || sources.length > 0)
          ? JSON.stringify({ steps, sources })
          : undefined;

      await addMessage.mutateAsync({
        convId: convId!,
        role: 'assistant',
        content: fullContent,
        metadata,
      });
      // Only clear on success: `messages` already carries the saved reply by
      // this point (addMessage's onSuccess invalidates+refetches before
      // mutateAsync resolves), so this can't leave a visible gap. Clearing
      // unconditionally in a `finally` used to wipe the error message below
      // in the same tick it was set — a failed save (or a failed stream)
      // silently vanished the whole reply with nothing shown to the user.
      setStreamingContent('');
    } catch (error) {
      setStreamingContent(
        `Error: ${error instanceof Error ? error.message : 'Failed to get response'}` +
          (fullContent ? `\n\n(reply was not saved)\n\n${fullContent}` : '')
      );
    } finally {
      setIsStreaming(false);
      setLiveSteps([]);
      // Deliberately *after* the reply, not alongside it. llama-server serves one
      // request at a time per model, so a classify fired in parallel simply
      // wins the queue and the user waits out a whole second generation before
      // their first token. The save/quiz prompts it produces are offered after
      // the reply anyway.
      if (!isWebSearch) classifyUserMessage(userMessage, userMsgResult.id);
    }
  };

  const handleSave = () => {
    if (!pendingSave || !conversationId) return;
    saveCalendar.mutate({
      conversationId,
      messageId: pendingSave.messageId,
      title: pendingSave.data.title,
      description: pendingSave.data.description || '',
      date: pendingSave.data.date || new Date().toISOString().split('T')[0],
      time: pendingSave.data.time,
      tags: pendingSave.data.tags,
    });
  };

  const toggleRecording = async () => {
    if (isRecording) {
      mediaRecorderRef.current?.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
          ? 'audio/webm'
          : 'audio/ogg';
      const recorder = new MediaRecorder(stream, { mimeType });
      audioChunksRef.current = [];
      recorder.ondataavailable = e => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        setIsRecording(false);
        const blob = new Blob(audioChunksRef.current, { type: mimeType });
        if (blob.size < 1000) return;
        setIsTranscribing(true);
        try {
          const ext = mimeType.includes('webm') ? '.webm' : '.ogg';
          const form = new FormData();
          form.append('audio', blob, `chat${ext}`);
          const r = await fetch('/api/transcribe', {
            method: 'POST',
            credentials: 'include',
            body: form,
          });
          if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(
              (err as { error?: string }).error || 'Transcription failed'
            );
          }
          const data = (await r.json()) as { text?: string };
          if (data.text?.trim()) {
            await sendMessage(data.text.trim());
          }
        } catch (err) {
          console.error('Voice transcription error:', err);
        } finally {
          setIsTranscribing(false);
        }
      };
      recorder.start();
      mediaRecorderRef.current = recorder;
      setIsRecording(true);
    } catch (err) {
      console.error('Microphone access error:', err);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const isConfigured = !!settings?.llamaUrl;
  const isSaving = saveCalendar.isPending;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex items-center justify-end border-b border-white/10 px-4 py-2">
        <button
          onClick={startNewChat}
          disabled={!hasChat || isStreaming}
          title="Clear the view and start a fresh chat (history stays saved above)"
          className="px-3 py-1 text-sm rounded-lg border border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-white/20 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          New chat
        </button>
      </div>
      <div
        ref={scrollContainerRef}
        className="flex-1 overflow-y-auto p-4 space-y-4"
      >
        {!isConfigured && (
          <div className="bg-yellow-900/30 border border-yellow-600/50 rounded-lg p-4 text-yellow-200">
            Please configure an AI provider in Settings to start chatting.
          </div>
        )}
        {!hasChat && isConfigured && (
          <div className="text-center text-[var(--color-text-muted)] py-12">
            <h2 className="text-xl mb-2">
              {isWebSearch ? 'Search the web' : 'Welcome to Lunaschal'}
            </h2>
            <p>
              {isWebSearch
                ? "Ask a question that depends on what's actually out there — it'll search and read pages before answering."
                : 'Start a conversation or ask me anything.'}
            </p>
            {!isWebSearch && (
              <p className="text-sm mt-4">
                Try: "Quiz me on React hooks", "note to self: ...", or "I went
                to the dentist"
              </p>
            )}
          </div>
        )}
        {messages.map(message => {
          if (message.role === 'system') {
            if (!isBreak(message)) return null;
            return (
              <div
                key={message.id}
                ref={message.id === lastBreakId ? lastBreakRef : undefined}
                className="flex items-center gap-3 py-1 text-xs text-[var(--color-text-muted)] select-none"
              >
                <div className="flex-1 h-px bg-white/10" />
                New chat
                {formatMessageTime(message.createdAt) && (
                  <span>· {formatMessageTime(message.createdAt)}</span>
                )}
                <div className="flex-1 h-px bg-white/10" />
              </div>
            );
          }
          const metadata = message.metadata
            ? JSON.parse(message.metadata)
            : null;
          const hasSaved =
            metadata?.savedAsJournal || metadata?.savedAsCalendar;
          // The overnight briefing's plan for the day — crossed off in place;
          // only an explicit "add to to-dos" ever reaches the list.
          const proposedTodos = parseProposedTodos(message.metadata);
          const { steps, sources } = parseWebSearchMeta(message.metadata);
          const sentAt = formatMessageTime(message.createdAt);
          return (
            <div
              key={message.id}
              className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div className="max-w-[80%]">
                <div
                  className={`content-text rounded-lg px-4 py-2 ${message.role === 'user' ? 'bg-[var(--color-primary)] text-white' : 'bg-[var(--color-surface)] text-[var(--color-text)]'}`}
                >
                  {message.role === 'user' ? (
                    <div className="whitespace-pre-wrap">{message.content}</div>
                  ) : (
                    <MessageMarkdown content={message.content} />
                  )}
                </div>
                {steps.length > 0 && (
                  <details className="mt-1 text-xs text-[var(--color-text-muted)]">
                    <summary className="cursor-pointer">
                      {steps.length} research step
                      {steps.length === 1 ? '' : 's'}
                    </summary>
                    <ul className="mt-1 space-y-0.5">
                      {steps.map((step, i) => (
                        <li key={i}>· {stepLabel(step)}</li>
                      ))}
                    </ul>
                  </details>
                )}
                {sources.length > 0 && (
                  <ul className="mt-1 text-xs space-y-0.5">
                    {sources.map(source => (
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
                    ))}
                  </ul>
                )}
                {proposedTodos.length > 0 && (
                  <BriefingTodos
                    messageId={message.id}
                    proposals={proposedTodos}
                  />
                )}
                {(hasSaved || sentAt) && (
                  <div
                    className={`mt-1 flex items-center gap-2 text-xs text-[var(--color-text-muted)] ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
                  >
                    {hasSaved && (
                      <span>
                        {metadata.savedAsJournal
                          ? 'Saved to journal'
                          : 'Saved to calendar'}
                      </span>
                    )}
                    {sentAt && (
                      <time dateTime={message.createdAt}>{sentAt}</time>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
        {isStreaming && liveSteps.length > 0 && (
          <div className="text-xs text-[var(--color-text-muted)] space-y-0.5">
            {liveSteps.map((step, i) => (
              <div key={i}>· {stepLabel(step)}</div>
            ))}
          </div>
        )}
        {streamingContent && (
          <div className="flex justify-start">
            <div className="max-w-[80%]">
              <div className="content-text rounded-lg px-4 py-2 bg-[var(--color-surface)] text-[var(--color-text)]">
                <MessageMarkdown content={streamingContent} />
              </div>
            </div>
          </div>
        )}
        {isStreaming && !streamingContent && (
          <div className="flex justify-start">
            <div className="bg-[var(--color-surface)] rounded-lg px-4 py-2 text-[var(--color-text-muted)]">
              Thinking...
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
        {/* After a "New chat" break, this gives the fresh segment room to pin
            to the top of the viewport with empty space below. */}
        {hasBreaks && <div aria-hidden className="min-h-[60vh]" />}
      </div>

      {queuedCards !== null && (
        <div className="border-t border-white/10 p-4 bg-[var(--color-surface)]/50">
          <div className="text-sm text-green-400">
            Queued {queuedCards} cards for approval — open the Learning tab to
            review and approve them.
          </div>
        </div>
      )}

      {noteCards && noteCards.length > 0 && (
        <div className="border-t border-white/10 p-4 bg-[var(--color-surface)]/50 space-y-3">
          <div className="text-sm font-medium text-[var(--color-text)]">
            Save {noteCards.length > 1 ? 'these lessons' : 'this lesson'} to
            Learning?
          </div>
          {noteCards.map(card => (
            <div
              key={card.id}
              className="border border-white/10 rounded-lg p-3 space-y-2"
            >
              <div className="text-sm text-[var(--color-text)]">
                <MessageMarkdown content={card.question} />
              </div>
              <div className="text-sm text-[var(--color-text-muted)]">
                <MessageMarkdown content={card.answer} />
              </div>

              {noteDupHint?.cardId === card.id && (
                <div className="text-xs text-yellow-400 space-y-1">
                  <div>
                    This looks {(noteDupHint.score * 100).toFixed(0)}% similar
                    to an existing card: "{noteDupHint.similar.question}"
                  </div>
                  <button
                    onClick={() =>
                      approveNoteCard.mutate({ id: card.id, force: true })
                    }
                    disabled={approveNoteCard.isPending}
                    className="underline hover:text-yellow-300 disabled:opacity-50"
                  >
                    Save anyway
                  </button>
                </div>
              )}

              {noteRegenId === card.id ? (
                <div className="flex gap-2">
                  <input
                    autoFocus
                    value={noteRegenDirection}
                    onChange={e => setNoteRegenDirection(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter' && noteRegenDirection.trim()) {
                        regenerateNoteCard.mutate({
                          id: card.id,
                          direction: noteRegenDirection.trim(),
                        });
                      }
                    }}
                    placeholder="What should change?"
                    className="flex-1 bg-[var(--color-bg)] border border-white/10 rounded px-2 py-1 text-sm text-[var(--color-text)] focus:outline-none focus:border-[var(--color-primary)]"
                  />
                  <button
                    onClick={() => {
                      setNoteRegenId(null);
                      setNoteRegenDirection('');
                    }}
                    disabled={regenerateNoteCard.isPending}
                    className="px-2 py-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() =>
                      regenerateNoteCard.mutate({
                        id: card.id,
                        direction: noteRegenDirection.trim(),
                      })
                    }
                    disabled={
                      !noteRegenDirection.trim() || regenerateNoteCard.isPending
                    }
                    className="px-2 py-1 text-sm bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
                  >
                    {regenerateNoteCard.isPending ? 'Updating...' : 'Update'}
                  </button>
                </div>
              ) : (
                <div className="flex gap-2 justify-end">
                  <button
                    onClick={() => discardNoteCard.mutate(card.id)}
                    disabled={discardNoteCard.isPending}
                    className="px-3 py-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
                  >
                    Discard
                  </button>
                  <button
                    onClick={() => {
                      setNoteRegenId(card.id);
                      setNoteRegenDirection('');
                    }}
                    className="px-3 py-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                  >
                    Request changes
                  </button>
                  <button
                    onClick={() => approveNoteCard.mutate({ id: card.id })}
                    disabled={approveNoteCard.isPending}
                    className="px-3 py-1 text-sm bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
                  >
                    Approve
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {pendingQuiz && (
        <div className="border-t border-white/10 p-4 bg-[var(--color-surface)]/50">
          <div className="flex items-start gap-3">
            <div className="flex-1">
              <div className="text-sm font-medium text-[var(--color-text)]">
                Generate flashcards for "{pendingQuiz.topic}"?
              </div>
              <div className="text-sm text-[var(--color-text-muted)] mt-1">
                I'll generate atomic cards and queue them for your approval in
                the Learning tab.
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setPendingQuiz(null)}
                disabled={generateForTopic.isPending}
                className="px-3 py-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
              >
                Dismiss
              </button>
              <button
                onClick={() => generateForTopic.mutate(pendingQuiz.topic)}
                disabled={generateForTopic.isPending}
                className="px-3 py-1 text-sm bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
              >
                {generateForTopic.isPending ? 'Generating...' : 'Queue Cards'}
              </button>
            </div>
          </div>
        </div>
      )}

      {pendingSave && (
        <div className="border-t border-white/10 p-4 bg-[var(--color-surface)]/50">
          <div className="flex items-start gap-3">
            <div className="flex-1">
              <div className="text-sm font-medium text-[var(--color-text)]">
                Save as calendar event?
              </div>
              <div className="text-sm text-[var(--color-text-muted)] mt-1">
                <span className="font-medium">{pendingSave.data.title}</span>
                {pendingSave.data.date && (
                  <span className="ml-2">({pendingSave.data.date})</span>
                )}
              </div>
              {pendingSave.data.tags.length > 0 && (
                <div className="flex gap-1 mt-2">
                  {pendingSave.data.tags.map(tag => (
                    <span
                      key={tag}
                      className="px-2 py-0.5 text-xs bg-white/10 rounded text-[var(--color-text-muted)]"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              )}
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setPendingSave(null)}
                disabled={isSaving}
                className="px-3 py-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
              >
                Dismiss
              </button>
              <button
                onClick={handleSave}
                disabled={isSaving}
                className="px-3 py-1 text-sm bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
              >
                {isSaving ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="border-t border-white/10 p-4">
        <div className="flex gap-2">
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              isConfigured
                ? isWebSearch
                  ? 'Ask something to look up...'
                  : 'Type a message...'
                : 'Configure AI provider first...'
            }
            disabled={!isConfigured || isStreaming}
            rows={1}
            className="flex-1 bg-[var(--color-surface)] border border-white/10 rounded-lg px-4 py-2 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none focus:border-[var(--color-primary)] disabled:opacity-50"
          />
          <button
            onClick={toggleRecording}
            disabled={!isConfigured || isStreaming || isTranscribing}
            title={isRecording ? 'Stop recording' : 'Speak to send'}
            className={`px-3 py-2 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
              isRecording
                ? 'bg-red-500 text-white animate-pulse hover:bg-red-500'
                : 'bg-[var(--color-surface)] border border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-white/20'
            }`}
          >
            {isTranscribing ? (
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
            onClick={() => sendMessage()}
            disabled={!input.trim() || !isConfigured || isStreaming}
            className="px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
