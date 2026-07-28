import { useState, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, ProposedTodo } from '../hooks/api';
import { MessageMarkdown } from './MessageMarkdown';
import { BriefingTodos } from './BriefingTodos';
import { contextMessages, isBreak } from '@/lib/chatSegments';
import { readSSE } from '@/lib/sse';

interface PendingSave {
  type: 'journal' | 'calendar';
  messageId: string;
  data: {
    title: string;
    content?: string;
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
    'journal' | 'calendar' | 'flashcard_request' | 'question' | 'conversation';
  confidence: number;
  journalEntry?: { title: string; content: string; tags: string[] };
  calendarEvent?: {
    title: string;
    description?: string;
    date?: string;
    time?: string;
    tags: string[];
  };
  flashcardRequest?: { topic: string };
}

const BREAK_METADATA = JSON.stringify({ break: true });

export function Chat() {
  const [input, setInput] = useState('');
  const [streamingContent, setStreamingContent] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [pendingSave, setPendingSave] = useState<PendingSave | null>(null);
  const [pendingQuiz, setPendingQuiz] = useState<PendingQuiz | null>(null);
  const [queuedCards, setQueuedCards] = useState<number | null>(null);
  const [ragContextUsed, setRagContextUsed] = useState(0);
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
    queryKey: ['chat', 'today'],
    queryFn: api.chat.today,
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });

  const invalidateToday = () =>
    queryClient.invalidateQueries({ queryKey: ['chat', 'today'] });

  const createConversation = useMutation({
    mutationFn: api.chat.createConversation,
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

  const saveJournal = useMutation({
    mutationFn: api.chat.saveJournal,
    onSuccess: () => {
      invalidateToday();
      queryClient.invalidateQueries({ queryKey: ['journal'] });
      setPendingSave(null);
    },
  });

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
  }, [messages, streamingContent, pendingSave, pendingQuiz, queuedCards]);

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
      const result = await createConversation.mutateAsync(undefined);
      convId = result.id;
    }

    const userMsgResult = await addMessage.mutateAsync({
      convId,
      role: 'user',
      content: userMessage,
    });

    classifyMessage.mutate(userMessage, {
      onSuccess: result => {
        const r = result as ClassifyResult;
        if (r.confidence >= 0.7) {
          if (r.intent === 'journal' && r.journalEntry) {
            setPendingSave({
              type: 'journal',
              messageId: userMsgResult.id,
              data: {
                title: r.journalEntry.title,
                content: r.journalEntry.content,
                tags: r.journalEntry.tags,
              },
            });
          } else if (r.intent === 'calendar' && r.calendarEvent) {
            setPendingSave({
              type: 'calendar',
              messageId: userMsgResult.id,
              data: {
                title: r.calendarEvent.title,
                description: r.calendarEvent.description,
                date: r.calendarEvent.date,
                time: r.calendarEvent.time,
                tags: r.calendarEvent.tags,
              },
            });
          } else if (r.intent === 'flashcard_request' && r.flashcardRequest) {
            setPendingQuiz({
              topic: r.flashcardRequest.topic,
              messageId: userMsgResult.id,
            });
          }
        }
      },
    });

    // Only the current segment (since the last "New chat") is sent to the model,
    // so the button acts as a true clear while history stays visible/saved.
    const chatMessages = [
      ...contextMessages(messages).map(m => ({
        role: m.role,
        content: m.content,
      })),
      { role: 'user' as const, content: userMessage },
    ];

    setIsStreaming(true);
    setStreamingContent('');
    setRagContextUsed(0);

    try {
      let ragContext: string | undefined;
      try {
        const ragResult = await api.chat.ragContext(userMessage, 3);
        if (ragResult.isConfigured && ragResult.context) {
          ragContext = ragResult.context;
          setRagContextUsed(ragResult.results.length);
        }
      } catch {
        // RAG is optional
      }

      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ messages: chatMessages, ragContext }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || 'Failed to get response');
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');

      let fullContent = '';
      for await (const parsed of readSSE(reader)) {
        if (parsed.content) {
          fullContent += parsed.content;
          setStreamingContent(fullContent);
        }
        if (parsed.error) throw new Error(parsed.error);
      }

      await addMessage.mutateAsync({
        convId: convId!,
        role: 'assistant',
        content: fullContent,
      });
    } catch (error) {
      setStreamingContent(
        `Error: ${error instanceof Error ? error.message : 'Failed to get response'}`
      );
    } finally {
      setIsStreaming(false);
      setStreamingContent('');
    }
  };

  const handleSave = () => {
    if (!pendingSave || !conversationId) return;
    if (pendingSave.type === 'journal') {
      saveJournal.mutate({
        conversationId,
        messageId: pendingSave.messageId,
        title: pendingSave.data.title,
        content: pendingSave.data.content || '',
        tags: pendingSave.data.tags,
      });
    } else {
      saveCalendar.mutate({
        conversationId,
        messageId: pendingSave.messageId,
        title: pendingSave.data.title,
        description: pendingSave.data.description || '',
        date: pendingSave.data.date || new Date().toISOString().split('T')[0],
        time: pendingSave.data.time,
        tags: pendingSave.data.tags,
      });
    }
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

  const isConfigured = !!settings?.ollamaUrl;
  const isSaving = saveJournal.isPending || saveCalendar.isPending;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-2">
        <h1 className="text-sm font-medium text-[var(--color-text-muted)]">
          Today's chat
        </h1>
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
            <h2 className="text-xl mb-2">Welcome to Lunaschal</h2>
            <p>
              Start a conversation, write in your journal, or ask me anything.
            </p>
            <p className="text-sm mt-4">
              Try: "Today I learned...", "Quiz me on React hooks", or "I went to
              the dentist"
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
                <div className="flex-1 h-px bg-white/10" />
              </div>
            );
          }
          const metadata = message.metadata
            ? JSON.parse(message.metadata)
            : null;
          const hasSaved =
            metadata?.savedAsJournal || metadata?.savedAsCalendar;
          // The overnight briefing proposes to-dos; they only reach the list
          // once accepted here.
          const proposedTodos: ProposedTodo[] = Array.isArray(
            metadata?.proposedTodos
          )
            ? metadata.proposedTodos
            : [];
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
                {proposedTodos.length > 0 && (
                  <BriefingTodos
                    messageId={message.id}
                    proposals={proposedTodos}
                  />
                )}
                {hasSaved && (
                  <div className="mt-1 text-xs text-[var(--color-text-muted)] text-right">
                    {metadata.savedAsJournal
                      ? 'Saved to journal'
                      : 'Saved to calendar'}
                  </div>
                )}
              </div>
            </div>
          );
        })}
        {isStreaming && streamingContent && (
          <div className="flex justify-start">
            <div className="max-w-[80%]">
              {ragContextUsed > 0 && (
                <div className="text-xs text-[var(--color-text-muted)] mb-1 flex items-center gap-1">
                  <span className="inline-block w-2 h-2 bg-green-500 rounded-full"></span>
                  Using {ragContextUsed} source
                  {ragContextUsed > 1 ? 's' : ''} from your knowledge base
                </div>
              )}
              <div className="content-text rounded-lg px-4 py-2 bg-[var(--color-surface)] text-[var(--color-text)]">
                <MessageMarkdown content={streamingContent} />
              </div>
            </div>
          </div>
        )}
        {isStreaming && !streamingContent && (
          <div className="flex justify-start">
            <div className="bg-[var(--color-surface)] rounded-lg px-4 py-2 text-[var(--color-text-muted)]">
              {ragContextUsed > 0
                ? 'Searching knowledge base...'
                : 'Thinking...'}
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
                {pendingSave.type === 'journal'
                  ? 'Save as journal entry?'
                  : 'Save as calendar event?'}
              </div>
              <div className="text-sm text-[var(--color-text-muted)] mt-1">
                <span className="font-medium">{pendingSave.data.title}</span>
                {pendingSave.type === 'calendar' && pendingSave.data.date && (
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
