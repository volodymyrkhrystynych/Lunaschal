import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api, type LearningCard } from '../../hooks/api';
import { MessageMarkdown } from '../MessageMarkdown';

interface Props {
  card: LearningCard;
  userAnswer?: string;
}

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

// 'auto' = folder's evidence provider (backend default), 'none' = model only,
// anything else is an mcp_servers id.
type Source = 'auto' | 'none' | string;

export function CardChat({ card, userAnswer }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [transcript, setTranscript] = useState<unknown[]>([]);
  const [input, setInput] = useState('');
  const [source, setSource] = useState<Source>('auto');

  const { data: servers } = useQuery({
    queryKey: ['learning', 'mcp-servers'],
    queryFn: api.learning.listMcpServers,
  });

  const send = useMutation({
    mutationFn: (message: string) =>
      api.learning.chat(card.id, {
        message,
        transcript: transcript.length ? transcript : undefined,
        mcpServerId:
          source === 'auto' ? undefined : source === 'none' ? null : source,
        userAnswer: transcript.length ? undefined : userAnswer,
      }),
    onSuccess: r => {
      setMessages(prev => [...prev, { role: 'assistant', content: r.reply }]);
      setTranscript(r.transcript);
    },
  });

  const submit = () => {
    const message = input.trim();
    if (!message || send.isPending) return;
    setMessages(prev => [...prev, { role: 'user', content: message }]);
    setInput('');
    send.mutate(message);
  };

  return (
    <div className="mt-4 pt-4 border-t border-white/10 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs text-[var(--color-text-muted)] uppercase tracking-wide">
          Discuss this card
        </div>
        <select
          value={source}
          onChange={e => setSource(e.target.value)}
          className="bg-[var(--color-surface)] text-xs text-[var(--color-text-muted)] border border-white/10 rounded px-2 py-1 focus:outline-none"
          title="Knowledge source the agent may consult"
        >
          <option value="auto">Folder source</option>
          <option value="none">Model only</option>
          {servers?.map(s => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </div>

      {messages.length > 0 && (
        <div className="space-y-2 max-h-72 overflow-y-auto">
          {messages.map((m, i) => (
            <div
              key={i}
              className={`text-sm rounded-lg px-3 py-2 ${
                m.role === 'user'
                  ? 'bg-[var(--color-primary)]/20 text-[var(--color-text)] ml-8'
                  : 'bg-white/5 text-[var(--color-text)] mr-8'
              }`}
            >
              <MessageMarkdown content={m.content} />
            </div>
          ))}
          {send.isPending && (
            <div className="text-sm text-[var(--color-text-muted)] animate-pulse mr-8 px-3">
              Thinking…
            </div>
          )}
        </div>
      )}

      {send.isError && (
        <p className="text-xs text-red-400">
          {send.error instanceof Error ? send.error.message : 'Chat failed'}
        </p>
      )}

      <div className="flex gap-2">
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') submit();
          }}
          placeholder="Ask for clarification or an example…"
          className="flex-1 bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]"
        />
        <button
          onClick={submit}
          disabled={!input.trim() || send.isPending}
          className="px-4 py-2 text-sm bg-white/10 text-[var(--color-text)] rounded-lg hover:bg-white/20 disabled:opacity-50"
        >
          {send.isPending ? '…' : 'Send'}
        </button>
      </div>
    </div>
  );
}
