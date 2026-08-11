import { useState, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type ChatAttachment, type DraftCard } from '../../hooks/api';
import { useRecorder } from '../../hooks/useRecorder';
import { MessageMarkdown } from '../MessageMarkdown';
import { BriefingTodos } from '../BriefingTodos';
import { AgentSteps } from './AgentSteps';
import { DelegateProposals } from './DelegateProposals';
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
import {
  parseAgentMeta,
  parseDelegateProposals,
  type AgentStep,
} from '@/lib/agentSteps';
import {
  ACCEPT_PHOTO,
  photoStatusMessage,
  photosFromTransfer,
  rejectedPhotosMessage,
} from '@/lib/chatAttachments';
import { currentPosition } from '@/lib/geo';

/** One staged action from the delegate's `done` event. Only `note` is still
 * read from this live shape — the other kinds (calendar/calorie/task/
 * flashcards) are durable confirm cards read from `message.metadata.proposals`
 * instead (parseDelegateProposals), so they survive a reload or a dropped
 * connection. `note` drafts immediately with no confirm step of its own. */
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
  const [noteCards, setNoteCards] = useState<DraftCard[] | null>(null);
  // Which drafted note card has its "request changes" text box open.
  const [noteRegenId, setNoteRegenId] = useState<string | null>(null);
  const [noteRegenDirection, setNoteRegenDirection] = useState('');
  const [noteDupHint, setNoteDupHint] = useState<{
    cardId: string;
    similar: { question: string; answer: string };
    score: number;
  } | null>(null);
  // Photos staged for the next message. They exist server-side already (the
  // reading has to start while the user is still talking — it runs on a CPU-only
  // model), so this holds real rows, not File objects.
  const [staged, setStaged] = useState<ChatAttachment[]>([]);
  const [attachError, setAttachError] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  // The verbatim transcript behind whatever is in the box, when it was dictated.
  // Kept so it can be shown, and so it rides to the server as the message's
  // `rawContent` — corrected or not, what was said must survive.
  const [dictated, setDictated] = useState<string | null>(null);
  const [isPolishing, setIsPolishing] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const photoInputRef = useRef<HTMLInputElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const lastBreakRef = useRef<HTMLDivElement>(null);
  // When set, the next scroll effect pins the newest break divider to the top
  // (the "New chat" clear) instead of scrolling to the bottom.
  const justBrokeRef = useRef(false);
  const queryClient = useQueryClient();

  // The single conversation for the current chat day (4am -> 4am).
  //
  // refetchInterval keeps polling while the last message is still
  // 'streaming' — the reply now generates on a background thread
  // (backend/delegate/runs.py) independent of any one connection, so this is
  // what picks it up whether we dropped mid-stream (sendMessage's catch below
  // just invalidates and lets this take over) or the page was reloaded while
  // a reply was still running.
  const { data: conversation } = useQuery({
    queryKey: ['chat', 'today', 'chat'],
    queryFn: () => api.chat.today(),
    refetchInterval: query => {
      const msgs = query.state.data?.messages ?? [];
      return msgs[msgs.length - 1]?.status === 'streaming' ? 1500 : false;
    },
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
      rawContent,
      attachmentIds,
    }: {
      convId: string;
      role: string;
      content: string;
      metadata?: string;
      rawContent?: string | null;
      attachmentIds?: string[];
    }) =>
      api.chat.addMessage(convId, {
        role,
        content,
        metadata,
        rawContent,
        attachmentIds,
      }),
    onSuccess: invalidateToday,
  });

  /** `note` is the one proposal kind with no confirm card of its own — it
   * drafts a lesson card immediately, and the draft *is* the review step —
   * so it's still read straight off the live `done` event rather than from
   * persisted metadata. The other four kinds are durable confirm cards
   * rendered from `message.metadata.proposals` (DelegateProposals) instead. */
  const stageNoteProposals = (proposals: DelegateProposal[]) => {
    for (const { kind, data } of proposals) {
      const content =
        typeof data?.content === 'string' ? data.content.trim() : '';
      if (kind === 'note' && content) {
        generateFromNote.mutate(content);
      }
    }
  };

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
  }, [messages, streamingContent, streamingReasoning, liveSteps, noteCards]);

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
    // A photo on its own is a complete message — "what is this?" is implied by
    // sending it — so an empty box no longer blocks the send.
    if ((!userMessage && staged.length === 0) || isStreaming) return;

    const attachmentIds = staged.map(a => a.id);
    // Only ever the transcript that produced *this* text, and only when it
    // differs — a typed message must leave rawContent null, exactly as a typed
    // journal entry does.
    const rawContent =
      dictated && dictated !== userMessage ? dictated : undefined;

    if (messageText === undefined) setInput('');
    setStaged([]);
    setDictated(null);
    setAttachError('');

    let convId = conversationId;

    if (!convId) {
      const result = await createConversation.mutateAsync();
      convId = result.id;
    }

    await addMessage.mutateAsync({
      convId,
      role: 'user',
      content: userMessage,
      rawContent,
      attachmentIds,
    });

    // Only the current segment (since the last "New chat") is sent to the model,
    // so the button acts as a true clear while history stays visible/saved.
    // createdAt rides along so the backend can prefix each turn with when it
    // was sent — the model is otherwise blind to gaps in the conversation.
    // attachmentIds ride along too: the server expands them into the readings of
    // the photos, since the chat model cannot see an image itself.
    const chatMessages = [
      ...contextMessages(messages).map(m => ({
        role: m.role,
        content: m.content,
        createdAt: m.createdAt,
        attachmentIds: (m.attachments ?? []).map(a => a.id),
      })),
      {
        role: 'user' as const,
        content: userMessage,
        createdAt: new Date().toISOString(),
        attachmentIds,
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
        body: JSON.stringify({
          messages: chatMessages,
          conversationId: convId,
        }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || 'Failed to get response');
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');

      // The reply now generates on a background thread (backend/delegate/
      // runs.py), independent of this connection — the assistant message row
      // already exists server-side by the time `response.ok` above is true.
      // So a drop from here on (a network blip, `readSSE` throwing, or the
      // backend forwarding its own `{error}` frame) isn't a dead end: the
      // thread keeps going and checkpoints the row regardless, and the
      // `today` query's refetchInterval picks up wherever it lands. Only a
      // failure *before* that point (caught below) is a real, nothing-ever-
      // started error worth showing.
      try {
        for await (const parsed of readSSE(reader)) {
          // Tool events arrive as each delegate call finishes, so a hand-off
          // reads as progress rather than a spinner.
          if (parsed.tool) {
            setLiveSteps(steps => [...steps, parsed as AgentStep]);
          }
          if (parsed.thinking) {
            setStreamingReasoning(reasoning => reasoning + parsed.thinking);
          }
          if (parsed.content) {
            fullContent += parsed.content;
            setStreamingContent(fullContent);
          }
          if (parsed.done && Array.isArray(parsed.proposals)) {
            proposals = parsed.proposals as DelegateProposal[];
          }
          if (parsed.error) throw new Error(parsed.error);
        }

        invalidateToday();
        setStreamingContent('');
        stageNoteProposals(proposals);
      } catch {
        // The backend already has this reply — see the comment above.
        invalidateToday();
        setStreamingContent('');
      }
    } catch (error) {
      setStreamingContent(
        `Error: ${error instanceof Error ? error.message : 'Failed to get response'}`
      );
    } finally {
      setIsStreaming(false);
      setLiveSteps([]);
      setStreamingReasoning('');
    }
  };

  /**
   * Dictation lands in the box, it doesn't send.
   *
   * This used to POST the transcript straight through as the message, which is
   * the one place in the app that did — every other view appends to a textarea
   * (BrainDump, IdeaCapture, Journal, FoodCapture) precisely so a mangled proper
   * noun can be fixed before it becomes a record. It also made the correction
   * pass below impossible: there was no moment between transcribing and sending.
   */
  const handleTranscript = async (text: string) => {
    const raw = text.trim();
    if (!raw) return;
    setInput(prev => (prev.trim() ? `${prev.trim()} ${raw}` : raw));
    setDictated(prev => (prev ? `${prev} ${raw}` : raw));

    // Correct it against the memory document and whatever the vision model read
    // out of the attached photos. Best-effort: the transcript is already in the
    // box, so a failure here costs the user nothing.
    setIsPolishing(true);
    try {
      const { corrected } = await api.chat.polishTranscript(
        raw,
        staged.map(a => a.id)
      );
      if (corrected && corrected !== raw) {
        setInput(prev => {
          // Only rewrite the part we just added; anything typed alongside it is
          // the user's and must not be touched.
          const trimmed = prev.trimEnd();
          return trimmed.endsWith(raw)
            ? trimmed.slice(0, trimmed.length - raw.length) + corrected
            : prev;
        });
      }
    } catch {
      // Nothing to do — the raw transcript stands.
    } finally {
      setIsPolishing(false);
    }
  };

  const recorder = useRecorder(handleTranscript);
  const isRecording = recorder.status === 'recording';
  const isTranscribing = recorder.status === 'transcribing';

  const toggleRecording = () =>
    isRecording ? recorder.stop() : recorder.start();

  const attachPhotos = async (files: File[]) => {
    if (files.length === 0) return;
    setAttachError('');
    setIsUploading(true);
    try {
      let convId = conversationId;
      if (!convId) convId = (await createConversation.mutateAsync()).id;
      // Asked for at attach time, which is the moment closest to the meal — and
      // kept only as a fallback for the photo's EXIF, which a pasted image has
      // already lost. Resolves null when denied or slow; that costs nothing.
      const coords = await currentPosition();
      const uploaded = await api.chat.uploadAttachments(convId, files, coords);
      setStaged(prev => [...prev, ...uploaded]);
    } catch (err) {
      setAttachError(
        err instanceof Error ? err.message : "Couldn't attach that photo"
      );
    } finally {
      setIsUploading(false);
    }
  };

  const removeStaged = async (id: string) => {
    setStaged(prev => prev.filter(a => a.id !== id));
    try {
      await api.chat.deleteAttachment(id);
    } catch {
      // The row is orphaned rather than leaked — it belongs to this
      // conversation and goes with it. Not worth putting an error in the way.
    }
  };

  /** Paste and drop are first-class: on a phone, exporting a photo to Files and
   * picking it back out is precisely the step being deleted. A paste carrying no
   * image falls through to the textarea untouched. */
  const handleTransfer = (
    data: DataTransfer | null,
    event: { preventDefault: () => void }
  ) => {
    const { accepted, rejected } = photosFromTransfer(data);
    if (accepted.length === 0 && rejected.length === 0) return;
    event.preventDefault();
    setAttachError(rejectedPhotosMessage(rejected) ?? '');
    void attachPhotos(accepted);
  };

  /** Poll the staged photos while any is still being read.
   *
   * There is no chat SSE event stream to push this on, and the reading runs on a
   * CPU-only model that takes real seconds — so the composer says "Reading the
   * photo…" and finds out when it lands, the same way the Learning grade poll
   * works. Stops the moment nothing is 'running'. */
  useEffect(() => {
    const pending = staged.filter(a => a.descriptionStatus === 'running');
    if (pending.length === 0) return;
    const timer = setInterval(async () => {
      const updated = await Promise.all(
        pending.map(a => api.chat.getAttachment(a.id).catch(() => null))
      );
      const byId = new Map(
        updated
          .filter((a): a is ChatAttachment => a != null)
          .map(a => [a.id, a])
      );
      setStaged(prev => prev.map(a => byId.get(a.id) ?? a));
    }, 1500);
    return () => clearInterval(timer);
  }, [staged]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const isConfigured = !!settings?.llamaUrl;
  // With neither the omni model nor a projector on the chat model, nothing can
  // read a photo — and the composer says so rather than showing a spinner that
  // never resolves, the mistake the Ideas sketch caption exists to warn about.
  const visionConfigured = !!settings?.llamaVisionModel;
  const chatVision = !!settings?.llamaChatVision;
  const photoStatus = photoStatusMessage(staged, visionConfigured, chatVision);

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
          const hasSaved = metadata?.savedAsJournal;
          // The overnight briefing's plan for the day — crossed off in place;
          // only an explicit "add to to-dos" ever reaches the list.
          const proposedTodos = parseProposedTodos(message.metadata);
          // Also covers replies saved by the retired web-search tab: they
          // carry the same {steps, sources} shape.
          const { steps, sources } = parseAgentMeta(message.metadata);
          // The delegate's confirm cards — durable across a reload or a
          // dropped connection, since the background run itself wrote them
          // (backend/delegate/runs.py), and only removed from "pending" by an
          // explicit accept/dismiss (DelegateProposals).
          const delegateProposals = parseDelegateProposals(message.metadata);
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
                  {(message.attachments ?? []).length > 0 && (
                    <div className="mb-2 flex flex-wrap gap-2">
                      {(message.attachments ?? []).map(attachment => (
                        <a
                          key={attachment.id}
                          href={attachment.url}
                          target="_blank"
                          rel="noreferrer noopener"
                        >
                          <img
                            src={attachment.url}
                            // The reading is the alt text on purpose: it is
                            // literally the description of this picture, and it
                            // is the only thing the model ever saw of it.
                            alt={attachment.description || 'Attached photo'}
                            className="max-h-48 rounded-lg"
                          />
                        </a>
                      ))}
                    </div>
                  )}
                  {message.role === 'user' ? (
                    <div className="whitespace-pre-wrap">{message.content}</div>
                  ) : (
                    <MessageMarkdown content={message.content} />
                  )}
                </div>
                {/* What was actually dictated, kept whenever the correction pass
                    or an edit changed it — the journal's "As captured". Only
                    ever present on a message that was spoken. */}
                {message.role === 'user' && message.rawContent && (
                  <details className="mt-1 text-xs text-[var(--color-text-muted)] text-right">
                    <summary className="cursor-pointer select-none">
                      As dictated
                    </summary>
                    <div className="mt-1 whitespace-pre-wrap text-left">
                      {message.rawContent}
                    </div>
                  </details>
                )}
                {/* live is set from the persisted status (not local isStreaming
                    state) so a reopened or reloaded tab shows the same
                    "N steps so far ·" pulse as a tab that never left — the
                    backend now checkpoints steps into metadata as they happen
                    (backend/delegate/runs.py), not just once the run ends. */}
                <AgentSteps
                  steps={steps}
                  live={message.status === 'streaming'}
                />
                {/* Only when NOT locally streaming: the ephemeral bubble below
                    already covers a reply this tab is actively watching. This
                    is what shows a reply that's still generating after a
                    reload, or after this tab lost the connection and is
                    waiting on the `today` query's poll to catch up. */}
                {message.role === 'assistant' &&
                  !isStreaming &&
                  message.status === 'streaming' && (
                    <div className="mt-1 text-xs text-[var(--color-text-muted)]">
                      <ThinkingLabel />
                    </div>
                  )}
                {message.role === 'assistant' && message.status === 'error' && (
                  <div className="mt-1 text-xs text-red-400">
                    Error: {message.error || 'The reply failed.'}
                  </div>
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
                {delegateProposals.length > 0 && (
                  <DelegateProposals
                    messageId={message.id}
                    proposals={delegateProposals}
                  />
                )}
                {(hasSaved || sentAt) && (
                  <div
                    className={`mt-1 flex items-center gap-2 text-xs text-[var(--color-text-muted)] ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
                  >
                    {hasSaved && <span>Saved to journal</span>}
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

      <div
        className="border-t border-white/10 p-4"
        onPaste={e => handleTransfer(e.clipboardData, e)}
        onDragOver={e => e.preventDefault()}
        onDrop={e => handleTransfer(e.dataTransfer, e)}
      >
        {staged.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {staged.map(attachment => (
              <div key={attachment.id} className="relative">
                <img
                  src={attachment.url}
                  alt={attachment.description || 'Attached photo'}
                  className="h-20 w-20 rounded-lg object-cover"
                />
                {attachment.descriptionStatus === 'running' && (
                  <div className="absolute inset-0 rounded-lg bg-black/40" />
                )}
                <button
                  onClick={() => removeStaged(attachment.id)}
                  aria-label="Remove photo"
                  title="Remove photo"
                  className="absolute -right-1 -top-1 h-5 w-5 rounded-full bg-[var(--color-surface)] border border-white/20 text-xs leading-none text-[var(--color-text)]"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
        {(photoStatus || attachError || dictated) && (
          <div className="mb-2 space-y-1 text-xs text-[var(--color-text-muted)]">
            {attachError && <div className="text-red-400">{attachError}</div>}
            {photoStatus && <div>{photoStatus}</div>}
            {isPolishing && <div>Checking the transcript…</div>}
            {/* Shown before sending, not just after: the point of no longer
                auto-submitting is that a mangled name can be caught here. */}
            {dictated && !isPolishing && dictated !== input.trim() && (
              <details>
                <summary className="cursor-pointer select-none">
                  As dictated
                </summary>
                <div className="mt-1 whitespace-pre-wrap">{dictated}</div>
              </details>
            )}
          </div>
        )}
        <div className="flex gap-2">
          <input
            ref={photoInputRef}
            type="file"
            accept={ACCEPT_PHOTO}
            multiple
            className="hidden"
            onChange={e => {
              void attachPhotos(Array.from(e.target.files ?? []));
              e.target.value = '';
            }}
          />
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
            onClick={() => photoInputRef.current?.click()}
            disabled={!isConfigured || isStreaming || isUploading}
            aria-label="Attach a photo"
            title="Attach a photo"
            className="px-3 py-2 rounded-lg bg-[var(--color-surface)] border border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-white/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
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
              <rect x="3" y="5" width="18" height="14" rx="2" />
              <circle cx="8.5" cy="10.5" r="1.5" />
              <path d="m21 15-4.5-4.5L7 20" />
            </svg>
          </button>
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
            disabled={
              (!input.trim() && staged.length === 0) ||
              !isConfigured ||
              isStreaming
            }
            className="px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
