import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../hooks/api';

interface ChatNavProps {
  currentConversationId: string | null;
  onSelect: (id: string | null) => void;
  isMobile?: boolean;
}

export function ChatNav({
  currentConversationId,
  onSelect,
  isMobile = false,
}: ChatNavProps) {
  const queryClient = useQueryClient();

  const { data: conversations } = useQuery({
    queryKey: ['chat', 'conversations'],
    queryFn: api.chat.listConversations,
  });

  const deleteConversation = useMutation({
    mutationFn: api.chat.deleteConversation,
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: ['chat', 'conversations'] });
      if (id === currentConversationId) onSelect(null);
    },
  });

  const rowClass = (id: string) =>
    `group flex items-center justify-between px-2 py-1.5 rounded cursor-pointer transition-colors ${
      currentConversationId === id
        ? 'bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
        : 'text-[var(--color-text)] hover:bg-white/10'
    }`;

  return (
    <div
      className={`${isMobile ? 'w-full' : 'w-64 shrink-0'} border-r border-white/10 bg-[var(--color-surface)] flex flex-col`}
    >
      <div className="flex items-center justify-between px-2 pt-3 pb-1">
        <span className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
          Conversations
        </span>
        <button
          onClick={() => onSelect(null)}
          className="text-xs px-1.5 rounded text-[var(--color-text-muted)] hover:text-[var(--color-primary)] hover:bg-white/10 transition-colors"
          title="New conversation"
        >
          +
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-2 pt-1">
        {conversations?.map(conv => (
          <div
            key={conv.id}
            className={rowClass(conv.id)}
            onClick={() => onSelect(conv.id)}
          >
            <span className="text-sm truncate flex-1 min-w-0">
              {conv.title || 'New Conversation'}
            </span>
            <button
              onClick={e => {
                e.stopPropagation();
                if (confirm(`Delete "${conv.title || 'New Conversation'}"?`))
                  deleteConversation.mutate(conv.id);
              }}
              className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-white/20 text-[var(--color-text-muted)] hover:text-red-400 transition-all shrink-0"
              title="Delete"
            >
              ✕
            </button>
          </div>
        ))}
        {conversations && conversations.length === 0 && (
          <div className="text-sm text-[var(--color-text-muted)] px-2 py-1">
            No conversations yet
          </div>
        )}
      </div>
    </div>
  );
}
