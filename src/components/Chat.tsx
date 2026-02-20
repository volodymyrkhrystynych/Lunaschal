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
}

export function Chat({ conversationId, onConversationChange }: ChatProps) {
  const [input, setInput] = useState('');
  const [streamingContent, setStreamingContent] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
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

  const messages: Message[] = conversation?.messages || [];

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingContent]);

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
    await addMessage.mutateAsync({
      conversationId: convId,
      role: 'user',
      content: userMessage,
    });

    // Prepare messages for API
    const chatMessages = [
      ...messages.map((m) => ({ role: m.role, content: m.content })),
      { role: 'user' as const, content: userMessage },
    ];

    // Start streaming
    setIsStreaming(true);
    setStreamingContent('');

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
            } catch (e) {
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

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const isConfigured = settings?.hasOpenaiKey || settings?.hasGoogleKey || settings?.aiProvider === 'ollama';

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
          </div>
        )}

        {messages.map((message) => (
          <div
            key={message.id}
            className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[80%] rounded-lg px-4 py-2 ${
                message.role === 'user'
                  ? 'bg-[var(--color-primary)] text-white'
                  : 'bg-[var(--color-surface)] text-[var(--color-text)]'
              }`}
            >
              <div className="whitespace-pre-wrap">{message.content}</div>
            </div>
          </div>
        ))}

        {isStreaming && streamingContent && (
          <div className="flex justify-start">
            <div className="max-w-[80%] rounded-lg px-4 py-2 bg-[var(--color-surface)] text-[var(--color-text)]">
              <div className="whitespace-pre-wrap">{streamingContent}</div>
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
      </div>

      {/* Input */}
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
    </div>
  );
}
