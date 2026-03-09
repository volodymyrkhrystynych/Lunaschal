import { useState, useRef, useEffect } from 'react';
import { trpc } from '../hooks/trpc';

interface ChatProps {
  conversationId: string | null;
  onConversationChange: (id: string) => void;
}

interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  metadata?: string | null;
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

export function Chat({ conversationId, onConversationChange }: ChatProps) {
  const [input, setInput] = useState('');
  const [streamingContent, setStreamingContent] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [pendingSave, setPendingSave] = useState<PendingSave | null>(null);
  const [pendingQuiz, setPendingQuiz] = useState<PendingQuiz | null>(null);
  const [quizCards, setQuizCards] = useState<Array<{ id: string; front: string; back: string }>>([]);
  const [quizIndex, setQuizIndex] = useState(0);
  const [showAnswer, setShowAnswer] = useState(false);
  const [ragContextUsed, setRagContextUsed] = useState<number>(0);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const utils = trpc.useUtils();

  const { data: conversation } = trpc.chat.getConversation.useQuery(
    { id: conversationId! },
    { enabled: !!conversationId }
  );

  const { data: settings } = trpc.settings.get.useQuery();

  const createConversation = trpc.chat.createConversation.useMutation({
    onSuccess: (data) => {
      utils.chat.listConversations.invalidate();
      onConversationChange(data.id);
    },
  });

  const addMessage = trpc.chat.addMessage.useMutation({
    onSuccess: () => {
      utils.chat.getConversation.invalidate({ id: conversationId! });
    },
  });

  const classifyMessage = trpc.chat.classifyMessage.useMutation();

  const saveJournal = trpc.chat.saveJournalFromChat.useMutation({
    onSuccess: () => {
      utils.chat.getConversation.invalidate({ id: conversationId! });
      utils.journal.list.invalidate();
      setPendingSave(null);
    },
  });

  const saveCalendar = trpc.chat.saveCalendarFromChat.useMutation({
    onSuccess: () => {
      utils.chat.getConversation.invalidate({ id: conversationId! });
      utils.calendar.listByRange.invalidate();
      setPendingSave(null);
    },
  });

  const generateForTopic = trpc.flashcard.generateForTopic.useMutation({
    onSuccess: async (result) => {
      // Fetch the generated cards
      const cards = await Promise.all(
        result.ids.map(async (id) => {
          const card = await utils.flashcard.get.fetch({ id });
          return card;
        })
      );
      setQuizCards(cards.filter((c): c is NonNullable<typeof c> => c !== null));
      setQuizIndex(0);
      setShowAnswer(false);
      setPendingQuiz(null);
    },
  });

  const reviewCard = trpc.flashcard.review.useMutation({
    onSuccess: () => {
      utils.flashcard.getDue.invalidate();
      utils.flashcard.getStats.invalidate();
    },
  });

  const messages: Message[] = conversation?.messages || [];

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingContent, pendingSave, pendingQuiz, quizCards]);

  const sendMessage = async () => {
    if (!input.trim() || isStreaming) return;

    const userMessage = input.trim();
    setInput('');

    let convId = conversationId;

    // Create conversation if needed
    if (!convId) {
      const result = await createConversation.mutateAsync({
        title: userMessage.slice(0, 50),
      });
      convId = result.id;
    }

    // Add user message
    const userMsgResult = await addMessage.mutateAsync({
      conversationId: convId,
      role: 'user',
      content: userMessage,
    });

    // Classify the message in the background
    classifyMessage.mutate(
      { message: userMessage },
      {
        onSuccess: (result) => {
          if (result.confidence >= 0.7) {
            if (result.intent === 'journal' && result.journalEntry) {
              setPendingSave({
                type: 'journal',
                messageId: userMsgResult.id,
                data: {
                  title: result.journalEntry.title,
                  content: result.journalEntry.content,
                  tags: result.journalEntry.tags,
                },
              });
            } else if (result.intent === 'calendar' && result.calendarEvent) {
              setPendingSave({
                type: 'calendar',
                messageId: userMsgResult.id,
                data: {
                  title: result.calendarEvent.title,
                  description: result.calendarEvent.description,
                  date: result.calendarEvent.date,
                  time: result.calendarEvent.time,
                  tags: result.calendarEvent.tags,
                },
              });
            } else if (result.intent === 'flashcard_request' && result.flashcardRequest) {
              setPendingQuiz({
                topic: result.flashcardRequest.topic,
                messageId: userMsgResult.id,
              });
            }
          }
        },
      }
    );

    // Prepare messages for API
    const chatMessages = [
      ...messages.map((m) => ({ role: m.role, content: m.content })),
      { role: 'user' as const, content: userMessage },
    ];

    // Start streaming
    setIsStreaming(true);
    setStreamingContent('');
    setRagContextUsed(0);

    try {
      // Fetch RAG context for the message
      let ragContext: string | undefined;
      try {
        const ragResult = await utils.chat.getRAGContext.fetch({
          message: userMessage,
          limit: 3,
        });
        if (ragResult.isConfigured && ragResult.context) {
          ragContext = ragResult.context;
          setRagContextUsed(ragResult.results.length);
        }
      } catch (ragError) {
        // RAG is optional, continue without it
        console.warn('RAG context fetch failed:', ragError);
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

        const chunk = decoder.decode(value);
        const lines = chunk.split('\n');

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6);
            if (data === '[DONE]') continue;

            try {
              const parsed = JSON.parse(data);
              if (parsed.content) {
                fullContent += parsed.content;
                setStreamingContent(fullContent);
              }
              if (parsed.error) {
                throw new Error(parsed.error);
              }
            } catch {
              // Ignore JSON parse errors for incomplete chunks
            }
          }
        }
      }

      // Save assistant message
      await addMessage.mutateAsync({
        conversationId: convId,
        role: 'assistant',
        content: fullContent,
      });
    } catch (error) {
      console.error('Chat error:', error);
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

  const handleStartQuiz = () => {
    if (!pendingQuiz) return;
    generateForTopic.mutate({ topic: pendingQuiz.topic });
  };

  const handleReview = (grade: number) => {
    const card = quizCards[quizIndex];
    if (!card) return;

    reviewCard.mutate(
      { id: card.id, grade },
      {
        onSuccess: () => {
          if (quizIndex < quizCards.length - 1) {
            setQuizIndex(quizIndex + 1);
            setShowAnswer(false);
          } else {
            // Quiz complete
            setQuizCards([]);
            setQuizIndex(0);
          }
        },
      }
    );
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const isConfigured =
    settings?.hasOpenaiKey || settings?.hasGoogleKey || settings?.aiProvider === 'ollama';

  const isSaving = saveJournal.isPending || saveCalendar.isPending;
  const isGenerating = generateForTopic.isPending;

  const currentCard = quizCards[quizIndex];

  const grades = [
    { value: 0, label: 'Again', color: 'bg-red-500' },
    { value: 1, label: 'Hard', color: 'bg-orange-500' },
    { value: 2, label: 'Good', color: 'bg-yellow-500' },
    { value: 3, label: 'Easy', color: 'bg-green-500' },
  ];

  return (
    <div className="flex-1 flex flex-col">
      {/* Messages */}
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
            <p className="text-sm mt-4">
              Try: "Today I learned...", "Quiz me on React hooks", or "I went to the dentist"
            </p>
          </div>
        )}

        {messages.map((message) => {
          const metadata = message.metadata ? JSON.parse(message.metadata) : null;
          const hasSaved = metadata?.savedAsJournal || metadata?.savedAsCalendar;

          return (
            <div
              key={message.id}
              className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div className="max-w-[80%]">
                <div
                  className={`rounded-lg px-4 py-2 ${
                    message.role === 'user'
                      ? 'bg-[var(--color-primary)] text-white'
                      : 'bg-[var(--color-surface)] text-[var(--color-text)]'
                  }`}
                >
                  <div className="whitespace-pre-wrap">{message.content}</div>
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
                <div className="whitespace-pre-wrap">{streamingContent}</div>
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

      {/* In-chat Quiz Mode */}
      {currentCard && (
        <div className="border-t border-white/10 p-4 bg-[var(--color-surface)]">
          <div className="max-w-lg mx-auto">
            <div className="text-center mb-2 text-sm text-[var(--color-text-muted)]">
              Card {quizIndex + 1} of {quizCards.length}
            </div>
            <div className="bg-white/5 rounded-lg p-6 min-h-[120px] flex items-center justify-center">
              <div className="text-lg text-[var(--color-text)] text-center">
                {showAnswer ? currentCard.back : currentCard.front}
              </div>
            </div>
            {!showAnswer ? (
              <button
                onClick={() => setShowAnswer(true)}
                className="w-full mt-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80"
              >
                Show Answer
              </button>
            ) : (
              <div className="mt-4">
                <div className="text-center text-sm text-[var(--color-text-muted)] mb-2">
                  How well did you know this?
                </div>
                <div className="grid grid-cols-4 gap-2">
                  {grades.map((grade) => (
                    <button
                      key={grade.value}
                      onClick={() => handleReview(grade.value)}
                      disabled={reviewCard.isPending}
                      className={`py-2 ${grade.color} text-white rounded hover:opacity-80 disabled:opacity-50`}
                    >
                      {grade.label}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <button
              onClick={() => {
                setQuizCards([]);
                setQuizIndex(0);
              }}
              className="w-full mt-2 py-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              End Quiz
            </button>
          </div>
        </div>
      )}

      {/* Pending Quiz Generation */}
      {pendingQuiz && !currentCard && (
        <div className="border-t border-white/10 p-4 bg-[var(--color-surface)]/50">
          <div className="flex items-start gap-3">
            <div className="flex-1">
              <div className="text-sm font-medium text-[var(--color-text)]">
                Generate flashcards for "{pendingQuiz.topic}"?
              </div>
              <div className="text-sm text-[var(--color-text-muted)] mt-1">
                I'll create flashcards to help you learn about this topic.
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setPendingQuiz(null)}
                disabled={isGenerating}
                className="px-3 py-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
              >
                Dismiss
              </button>
              <button
                onClick={handleStartQuiz}
                disabled={isGenerating}
                className="px-3 py-1 text-sm bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
              >
                {isGenerating ? 'Generating...' : 'Start Quiz'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Pending Save Confirmation */}
      {pendingSave && !currentCard && (
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
                  {pendingSave.data.tags.map((tag) => (
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

      {/* Input */}
      {!currentCard && (
        <div className="border-t border-white/10 p-4">
          <div className="flex gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={isConfigured ? 'Type a message...' : 'Configure AI provider first...'}
              disabled={!isConfigured || isStreaming}
              rows={1}
              className="flex-1 bg-[var(--color-surface)] border border-white/10 rounded-lg px-4 py-2 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none focus:border-[var(--color-primary)] disabled:opacity-50"
            />
            <button
              onClick={sendMessage}
              disabled={!input.trim() || !isConfigured || isStreaming}
              className="px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Send
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
