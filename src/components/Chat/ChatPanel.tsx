import { useState, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type DraftCard } from '../../hooks/api';
import { MessageMarkdown } from '../MessageMarkdown';
import { BriefingTodos } from '../BriefingTodos';
import { AgentSteps } from './AgentSteps';
import { ReasoningBlock } from './ReasoningBlock';
import { ThinkingLabel } from './ThinkingLabel';
import {
  contextMessages,
  isBreak,
  parseProposedTodos,
} from '@/lib/chatSegments';
import { readSSE } from '@/lib/sse';
import { isAtBottom } from '@/lib/chatScroll';
import { formatMessageTime } from '@/lib/chatTime';
import { parseAgentMeta, type AgentStep } from '@/lib/agentSteps';

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

interface PendingCalorie {
  messageId: string;
  data: { description: string; calories: number };
}

interface PendingTask {
  messageId: string;
  data: { title: string; list?: string };
}

/** One staged action from the delegate's `done` event. The delegate writes
 * nothing itself — these become the same confirm cards below that the retired
 * classifier used to fill, so the click that actually saves is unchanged. */
interface DelegateProposal {
  kind: string;
  data: Record<string, unknown>;
}

const BREAK_METADATA = JSON.stringify({ break: true });

export function ChatPanel() {
  const [input, setInput] = useState('');
  const [streamingContent, setStreamingContent] = useState('');
  const [streamingReasoning, setStreamingReasoning] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [liveSteps, setLiveSteps] = useState<AgentStep[]>([]);
  const [pendingSave, setPendingSave] = useState<PendingSave | null>(null);
  const [pendingQuiz, setPendingQuiz] = useState<PendingQuiz | null>(null);
  const [pendingCalorie, setPendingCalorie] = useState<PendingCalorie | null>(
    null
  );
  const [pendingTask, setPendingTask] = useState<PendingTask | null>(null);
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

  // The single conversation for the current chat day (4am -> 4am).
  const { data: conversation } = useQuery({
    queryKey: ['chat', 'today', 'chat'],
    queryFn: () => api.chat.today(),
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });

  const invalidateToday = () =>
    queryClient.invalidateQueries({ queryKey: ['chat', 'today', 'chat'] });

  const createConversation = useMutation({
    mutationFn: () => api.chat.createConversation(),
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

  /** Turn what the delegate staged into confirm cards.
   *
   * The delegate decided *before* the reply and its tools validated the
   * payloads, so there is no confidence threshold to apply here — the old
   * classifier's 0.7 gate existed because it was guessing after the fact, and
   * it silently dropped everything below the line with nothing shown. A
   * proposal that arrives is a proposal the model committed to. */
  const applyProposals = (proposals: DelegateProposal[], messageId: string) => {
    for (const { kind, data } of proposals) {
      // The delegate's tools validate before staging, so a payload missing its
      // headline field can't come from our own backend — but an empty card the
      // user is asked to confirm is a worse failure than a dropped one, so the
      // required field is re-checked here rather than trusted.
      const str = (key: string) =>
        typeof data?.[key] === 'string' ? (data[key] as string).trim() : '';
      if (kind === 'calendar' && str('title')) {
        setPendingSave({
          messageId,
          data: {
            title: str('title'),
            description: str('description'),
            date: str('date'),
            time: str('time') || undefined,
            tags: Array.isArray(data.tags) ? (data.tags as string[]) : [],
          },
        });
      } else if (kind === 'flashcards' && str('topic')) {
        setPendingQuiz({ topic: str('topic'), messageId });
      } else if (kind === 'note' && str('content')) {
        // The only staged action with no confirm card of its own: it drafts
        // cards immediately, and the draft *is* the review step.
        generateFromNote.mutate(str('content'));
      } else if (
        kind === 'calorie' &&
        str('description') &&
        typeof data.calories === 'number'
      ) {
        setPendingCalorie({
          messageId,
          data: { description: str('description'), calories: data.calories },
        });
      } else if (kind === 'task' && str('title')) {
        setPendingTask({
          messageId,
          data: { title: str('title'), list: str('list') || undefined },
        });
      }
    }
  };

  const saveCalendar = useMutation({
    mutationFn: api.chat.saveCalendar,
    onSuccess: () => {
      invalidateToday();
      queryClient.invalidateQueries({ queryKey: ['calendar'] });
      setPendingSave(null);
    },
  });

  const saveCalories = useMutation({
    mutationFn: api.chat.saveCalories,
    onSuccess: () => {
      invalidateToday();
      queryClient.invalidateQueries({ queryKey: ['lifestyle', 'calories'] });
      setPendingCalorie(null);
    },
  });

  const saveTask = useMutation({
    mutationFn: api.chat.saveTask,
    onSuccess: () => {
      invalidateToday();
      queryClient.invalidateQueries({ queryKey: ['todos'] });
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      setPendingTask(null);
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
  // A break with nothing after it yet — the only time the spacer below is
  // wanted. Once the conversation resumes, its own content provides the room.
  const isTrailingBreak =
    messages.length > 0 && isBreak(messages[messages.length - 1]);
  // Only real user/assistant turns count toward "is there anything to clear".
  const hasChat = messages.some(m => m.role !== 'system');
  // The id of the last break marker, so only that divider carries the ref.
  const lastBreakId = [...messages].reverse().find(isBreak)?.id ?? null;

  // Whether the view should follow new content. A ref, not state: it is read
  // and written inside a scroll handler that fires at frame rate, and it must
  // never itself cause a render.
  const stickToBottomRef = useRef(true);

  const handleTranscriptScroll = () => {
    const c = scrollContainerRef.current;
    if (c) stickToBottomRef.current = isAtBottom(c);
  };

  useEffect(() => {
    if (justBrokeRef.current) {
      justBrokeRef.current = false;
      stickToBottomRef.current = false;
      const c = scrollContainerRef.current;
      const b = lastBreakRef.current;
      if (c && b) {
        const cRect = c.getBoundingClientRect();
        const bRect = b.getBoundingClientRect();
        c.scrollTop += bRect.top - cRect.top - 8;
      }
      return;
    }
    // Scroll the transcript itself rather than calling scrollIntoView on a
    // sentinel inside it. scrollIntoView walks *every* scrollable ancestor, and
    // `overflow: hidden` does not stop it — it only hides the scrollbar and
    // blocks the user. The app shell is `h-dvh` inside a `height: 100%` body,
    // so whenever dvh exceeds that (always on mobile with browser chrome up)
    // the body overflows by a few pixels, and scrollIntoView scrolled *the
    // body* — lurching the header, sidebar and SttPanel up with no scrollbar to
    // explain why. Setting scrollTop can only ever move this one element.
    const c = scrollContainerRef.current;
    const end = messagesEndRef.current;
    if (!c || !end || !stickToBottomRef.current) return;
    // Aim at the end-of-messages sentinel, not at scrollHeight: the sentinel
    // sits *above* the break spacer below, so this lands on the last message
    // instead of on empty space.
    //
    // Instant, not smooth: during a delegate run this fires on every step and
    // every reasoning delta, and overlapping smooth animations interrupt each
    // other into one continuous drift.
    const delta =
      end.getBoundingClientRect().bottom - c.getBoundingClientRect().bottom;
    // Only ever chase content *downward*. Correcting upward would fight a user
    // who is deliberately looking at something higher up.
    if (delta > 0) c.scrollTop += delta;
  }, [
    messages,
    streamingContent,
    streamingReasoning,
    liveSteps,
    pendingSave,
    pendingQuiz,
    pendingCalorie,
    pendingTask,
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
    setStreamingReasoning('');
    setLiveSteps([]);

    let fullContent = '';
    let proposals: DelegateProposal[] = [];
    try {
      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ messages: chatMessages }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || 'Failed to get response');
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');

      let steps: AgentStep[] = [];
      let sources: { url: string; title?: string }[] = [];
      let reasoning = '';
      for await (const parsed of readSSE(reader)) {
        // Tool events arrive as each delegate call finishes, so a hand-off
        // reads as progress rather than a spinner.
        if (parsed.tool) {
          steps = [...steps, parsed as AgentStep];
          setLiveSteps(steps);
        }
        if (parsed.thinking) {
          reasoning += parsed.thinking;
          setStreamingReasoning(reasoning);
        }
        if (parsed.content) {
          fullContent += parsed.content;
          setStreamingContent(fullContent);
        }
        if (parsed.done) {
          if (Array.isArray(parsed.sources)) {
            sources = parsed.sources as { url: string; title?: string }[];
          }
          if (Array.isArray(parsed.proposals)) {
            proposals = parsed.proposals as DelegateProposal[];
          }
        }
        if (parsed.error) throw new Error(parsed.error);
      }

      // Reasoning is deliberately not persisted: it is the model talking to
      // itself, it dwarfs the reply, and re-reading it a day later has never
      // once been the thing anyone wanted out of the chat log.
      const metadata =
        steps.length > 0 || sources.length > 0
          ? JSON.stringify({ agent: 'delegate', steps, sources })
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
      // Cards go up only once the reply they refer to is saved and on screen,
      // so "I've put that on your list to confirm" and the card appear together.
      applyProposals(proposals, userMsgResult.id);
    } catch (error) {
      setStreamingContent(
        `Error: ${error instanceof Error ? error.message : 'Failed to get response'}` +
          (fullContent ? `\n\n(reply was not saved)\n\n${fullContent}` : '')
      );
    } finally {
      setIsStreaming(false);
      setLiveSteps([]);
      setStreamingReasoning('');
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

  const handleSaveCalories = () => {
    if (!pendingCalorie) return;
    saveCalories.mutate({
      messageId: pendingCalorie.messageId,
      description: pendingCalorie.data.description,
      calories: pendingCalorie.data.calories,
    });
  };

  const handleSaveTask = () => {
    if (!pendingTask) return;
    saveTask.mutate({
      messageId: pendingTask.messageId,
      title: pendingTask.data.title,
      list: pendingTask.data.list,
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
  const isSavingCalories = saveCalories.isPending;
  const isSavingTask = saveTask.isPending;

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
        onScroll={handleTranscriptScroll}
        className="flex-1 overflow-y-auto p-4 space-y-4"
      >
        {!isConfigured && (
          <div className="bg-yellow-900/30 border border-yellow-600/50 rounded-lg p-4 text-yellow-200">
            Please configure an AI provider in Settings to start chatting.
          </div>
        )}
        {!hasChat && isConfigured && (
          <div className="text-center text-[var(--color-text-muted)] py-12">
            <h2 className="text-xl mb-2">Welcome to Lunaschal</h2>
            <p>
              Start a conversation, ask me anything, or ask me to do something.
            </p>
            <p className="text-sm mt-4">
              Try: "Quiz me on React hooks", "note to self: ...", "remind me to
              call the dentist", or "what's the latest on ..."
            </p>
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
            metadata?.savedAsJournal ||
            metadata?.savedAsCalendar ||
            metadata?.savedAsCalories ||
            metadata?.savedAsTask;
          // The overnight briefing's plan for the day — crossed off in place;
          // only an explicit "add to to-dos" ever reaches the list.
          const proposedTodos = parseProposedTodos(message.metadata);
          // Also covers replies saved by the retired web-search tab: they
          // carry the same {steps, sources} shape.
          const { steps, sources } = parseAgentMeta(message.metadata);
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
                <AgentSteps steps={steps} />
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
                          : metadata.savedAsCalories
                            ? 'Logged calories'
                            : metadata.savedAsTask
                              ? 'Added to tasks'
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
        {(streamingContent || (isStreaming && streamingReasoning)) && (
          <div className="flex justify-start">
            <div className="max-w-[80%]">
              {streamingContent && (
                <div className="content-text rounded-lg px-4 py-2 bg-[var(--color-surface)] text-[var(--color-text)]">
                  <MessageMarkdown content={streamingContent} />
                </div>
              )}
              <ReasoningBlock content={streamingReasoning} live={isStreaming} />
              <AgentSteps steps={liveSteps} live />
            </div>
          </div>
        )}
        {isStreaming && !streamingContent && !streamingReasoning && (
          <div className="flex justify-start">
            <div className="max-w-[80%]">
              <div className="bg-[var(--color-surface)] rounded-lg px-4 py-2 text-[var(--color-text-muted)]">
                <ThinkingLabel />
              </div>
              <AgentSteps steps={liveSteps} live />
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
        {/* Room for a fresh segment to pin to the top of the viewport after a
            "New chat" break. Rendered only while the break is still the last
            thing in the transcript: keyed on `hasBreaks` it stayed for the rest
            of the day, leaving 60vh of empty space you could scroll into long
            after the conversation had resumed and filled the screen itself. */}
        {isTrailingBreak && <div aria-hidden className="min-h-[60vh]" />}
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

      {pendingCalorie && (
        <div className="border-t border-white/10 p-4 bg-[var(--color-surface)]/50">
          <div className="flex items-start gap-3">
            <div className="flex-1">
              <div className="text-sm font-medium text-[var(--color-text)]">
                Log calories?
              </div>
              <div className="text-sm text-[var(--color-text-muted)] mt-1">
                <span className="font-medium">
                  {pendingCalorie.data.description}
                </span>
                <span className="ml-2">
                  ({pendingCalorie.data.calories} cal)
                </span>
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setPendingCalorie(null)}
                disabled={isSavingCalories}
                className="px-3 py-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
              >
                Dismiss
              </button>
              <button
                onClick={handleSaveCalories}
                disabled={isSavingCalories}
                className="px-3 py-1 text-sm bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
              >
                {isSavingCalories ? 'Logging...' : 'Log'}
              </button>
            </div>
          </div>
        </div>
      )}

      {pendingTask && (
        <div className="border-t border-white/10 p-4 bg-[var(--color-surface)]/50">
          <div className="flex items-start gap-3">
            <div className="flex-1">
              <div className="text-sm font-medium text-[var(--color-text)]">
                Add to your tasks?
              </div>
              <div className="text-sm text-[var(--color-text-muted)] mt-1">
                <span className="font-medium">{pendingTask.data.title}</span>
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setPendingTask(null)}
                disabled={isSavingTask}
                className="px-3 py-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
              >
                Dismiss
              </button>
              <button
                onClick={handleSaveTask}
                disabled={isSavingTask}
                className="px-3 py-1 text-sm bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
              >
                {isSavingTask ? 'Adding...' : 'Add'}
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
                ? 'Type a message...'
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
