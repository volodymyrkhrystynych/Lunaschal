import { useState, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../hooks/api';
import { MessageMarkdown } from './MessageMarkdown';
import { ChatNav } from './ChatNav';

interface ChatProps {
  conversationId: string | null;
  onConversationChange: (id: string | null) => void;
}

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
  intent: 'journal' | 'calendar' | 'flashcard_request' | 'question' | 'conversation';
  confidence: number;
  journalEntry?: { title: string; content: string; tags: string[] };
  calendarEvent?: { title: string; description?: string; date?: string; time?: string; tags: string[] };
  flashcardRequest?: { topic: string };
}

export function Chat({ conversationId, onConversationChange }: ChatProps) {
  const [input, setInput] = useState('');
  const [streamingContent, setStreamingContent] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [pendingSave, setPendingSave] = useState<PendingSave | null>(null);
  const [pendingQuiz, setPendingQuiz] = useState<PendingQuiz | null>(null);
  const [queuedCards, setQueuedCards] = useState<number | null>(null);
  const [ragContextUsed, setRagContextUsed] = useState(0);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  const { data: conversation } = useQuery({
    queryKey: ['chat', 'conversation', conversationId],
    queryFn: () => api.chat.getConversation(conversationId!),
    enabled: !!conversationId,
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });

  const createConversation = useMutation({
    mutationFn: api.chat.createConversation,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['chat', 'conversations'] });
      onConversationChange(data.id);
    },
  });

  const addMessage = useMutation({
    mutationFn: ({ convId, role, content }: { convId: string; role: string; content: string }) =>
      api.chat.addMessage(convId, { role, content }),
    onSuccess: (_, vars) =>
      queryClient.invalidateQueries({ queryKey: ['chat', 'conversation', vars.convId] }),
  });

  const classifyMessage = useMutation({
    mutationFn: (message: string) => api.chat.classify(message),
  });

  const saveJournal = useMutation({
    mutationFn: api.chat.saveJournal,
    onSuccess: (_, vars) => {
      queryClient.invalidateQueries({ queryKey: ['chat', 'conversation', vars.conversationId] });
      queryClient.invalidateQueries({ queryKey: ['journal'] });
      setPendingSave(null);
    },
  });

  const saveCalendar = useMutation({
    mutationFn: api.chat.saveCalendar,
    onSuccess: (_, vars) => {
      queryClient.invalidateQueries({ queryKey: ['chat', 'conversation', vars.conversationId] });
      queryClient.invalidateQueries({ queryKey: ['calendar'] });
      setPendingSave(null);
    },
  });

  const generateForTopic = useMutation({
    mutationFn: (topic: string) => api.learning.generateForTopic(topic),
    onSuccess: (result) => {
      setQueuedCards(result.count);
      setPendingQuiz(null);
      queryClient.invalidateQueries({ queryKey: ['learning'] });
      setTimeout(() => setQueuedCards(null), 8000);
    },
  });

  const messages = conversation?.messages || [];

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingContent, pendingSave, pendingQuiz, queuedCards]);

  const sendMessage = async () => {
    if (!input.trim() || isStreaming) return;

    const userMessage = input.trim();
    setInput('');

    let convId = conversationId;

    if (!convId) {
      const result = await createConversation.mutateAsync({ title: userMessage.slice(0, 50) });
      convId = result.id;
    }

    const userMsgResult = await addMessage.mutateAsync({ convId, role: 'user', content: userMessage });

    classifyMessage.mutate(userMessage, {
      onSuccess: (result) => {
        const r = result as ClassifyResult;
        if (r.confidence >= 0.7) {
          if (r.intent === 'journal' && r.journalEntry) {
            setPendingSave({
              type: 'journal',
              messageId: userMsgResult.id,
              data: { title: r.journalEntry.title, content: r.journalEntry.content, tags: r.journalEntry.tags },
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
            setPendingQuiz({ topic: r.flashcardRequest.topic, messageId: userMsgResult.id });
          }
        }
      },
    });

    const chatMessages = [
      ...messages.map((m) => ({ role: m.role, content: m.content })),
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

      const decoder = new TextDecoder();
      let fullContent = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const lines = decoder.decode(value).split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6);
            if (data === '[DONE]') continue;
            try {
              const parsed = JSON.parse(data);
              if (parsed.content) { fullContent += parsed.content; setStreamingContent(fullContent); }
              if (parsed.error) throw new Error(parsed.error);
            } catch { /* ignore parse errors */ }
          }
        }
      }

      await addMessage.mutateAsync({ convId: convId!, role: 'assistant', content: fullContent });
    } catch (error) {
      setStreamingContent(`Error: ${error instanceof Error ? error.message : 'Failed to get response'}`);
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

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  };

  const isConfigured = settings?.hasOpenaiKey || settings?.hasGoogleKey || settings?.aiProvider === 'ollama';
  const isSaving = saveJournal.isPending || saveCalendar.isPending;

  return (
    <div className="flex-1 flex overflow-hidden">
      <ChatNav currentConversationId={conversationId} onSelect={onConversationChange} />
      <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {!isConfigured && (
          <div className="bg-yellow-900/30 border border-yellow-600/50 rounded-lg p-4 text-yellow-200">
            Please configure an AI provider in Settings to start chatting.
          </div>
        )}
        {messages.length === 0 && isConfigured && (
          <div className="text-center text-[var(--color-text-muted)] py-12">
            <h2 className="text-xl mb-2">Welcome to Lunaschal</h2>
            <p>Start a conversation, write in your journal, or ask me anything.</p>
            <p className="text-sm mt-4">Try: "Today I learned...", "Quiz me on React hooks", or "I went to the dentist"</p>
          </div>
        )}
        {messages.map((message) => {
          const metadata = message.metadata ? JSON.parse(message.metadata) : null;
          const hasSaved = metadata?.savedAsJournal || metadata?.savedAsCalendar;
          return (
            <div key={message.id} className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className="max-w-[80%]">
                <div className={`rounded-lg px-4 py-2 ${message.role === 'user' ? 'bg-[var(--color-primary)] text-white' : 'bg-[var(--color-surface)] text-[var(--color-text)]'}`}>
                  {message.role === 'user' ? (
                    <div className="whitespace-pre-wrap">{message.content}</div>
                  ) : (
                    <MessageMarkdown content={message.content} />
                  )}
                </div>
                {hasSaved && (
                  <div className="mt-1 text-xs text-[var(--color-text-muted)] text-right">
                    {metadata.savedAsJournal ? 'Saved to journal' : 'Saved to calendar'}
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
                  Using {ragContextUsed} source{ragContextUsed > 1 ? 's' : ''} from your knowledge base
                </div>
              )}
              <div className="rounded-lg px-4 py-2 bg-[var(--color-surface)] text-[var(--color-text)]">
                <MessageMarkdown content={streamingContent} />
              </div>
            </div>
          </div>
        )}
        {isStreaming && !streamingContent && (
          <div className="flex justify-start">
            <div className="bg-[var(--color-surface)] rounded-lg px-4 py-2 text-[var(--color-text-muted)]">
              {ragContextUsed > 0 ? 'Searching knowledge base...' : 'Thinking...'}
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {queuedCards !== null && (
        <div className="border-t border-white/10 p-4 bg-[var(--color-surface)]/50">
          <div className="text-sm text-green-400">
            Queued {queuedCards} cards for approval — open the Learning tab to review and approve them.
          </div>
        </div>
      )}

      {pendingQuiz && (
        <div className="border-t border-white/10 p-4 bg-[var(--color-surface)]/50">
          <div className="flex items-start gap-3">
            <div className="flex-1">
              <div className="text-sm font-medium text-[var(--color-text)]">Generate flashcards for "{pendingQuiz.topic}"?</div>
              <div className="text-sm text-[var(--color-text-muted)] mt-1">I'll generate atomic cards and queue them for your approval in the Learning tab.</div>
            </div>
            <div className="flex gap-2">
              <button onClick={() => setPendingQuiz(null)} disabled={generateForTopic.isPending} className="px-3 py-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50">Dismiss</button>
              <button onClick={() => generateForTopic.mutate(pendingQuiz.topic)} disabled={generateForTopic.isPending}
                className="px-3 py-1 text-sm bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">
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
                {pendingSave.type === 'journal' ? 'Save as journal entry?' : 'Save as calendar event?'}
              </div>
              <div className="text-sm text-[var(--color-text-muted)] mt-1">
                <span className="font-medium">{pendingSave.data.title}</span>
                {pendingSave.type === 'calendar' && pendingSave.data.date && <span className="ml-2">({pendingSave.data.date})</span>}
              </div>
              {pendingSave.data.tags.length > 0 && (
                <div className="flex gap-1 mt-2">
                  {pendingSave.data.tags.map((tag) => (
                    <span key={tag} className="px-2 py-0.5 text-xs bg-white/10 rounded text-[var(--color-text-muted)]">{tag}</span>
                  ))}
                </div>
              )}
            </div>
            <div className="flex gap-2">
              <button onClick={() => setPendingSave(null)} disabled={isSaving} className="px-3 py-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50">Dismiss</button>
              <button onClick={handleSave} disabled={isSaving}
                className="px-3 py-1 text-sm bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">
                {isSaving ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="border-t border-white/10 p-4">
        <div className="flex gap-2">
          <textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={handleKeyDown}
            placeholder={isConfigured ? 'Type a message...' : 'Configure AI provider first...'}
            disabled={!isConfigured || isStreaming} rows={1}
            className="flex-1 bg-[var(--color-surface)] border border-white/10 rounded-lg px-4 py-2 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none focus:border-[var(--color-primary)] disabled:opacity-50" />
          <button onClick={sendMessage} disabled={!input.trim() || !isConfigured || isStreaming}
            className="px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
            Send
          </button>
        </div>
      </div>
      </div>
    </div>
  );
}
