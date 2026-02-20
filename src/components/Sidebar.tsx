import { trpc } from '../hooks/trpc';

type View = 'chat' | 'journal' | 'calendar' | 'flashcards' | 'settings';

interface SidebarProps {
  currentView: View;
  onViewChange: (view: View) => void;
  isOpen: boolean;
  onToggle: () => void;
  currentConversationId: string | null;
  onConversationSelect: (id: string | null) => void;
}

const navItems: { view: View; label: string; icon: string }[] = [
  { view: 'chat', label: 'Chat', icon: '💬' },
  { view: 'journal', label: 'Journal', icon: '📓' },
  { view: 'calendar', label: 'Calendar', icon: '📅' },
  { view: 'flashcards', label: 'Flashcards', icon: '🎴' },
  { view: 'settings', label: 'Settings', icon: '⚙️' },
];

export function Sidebar({
  currentView,
  onViewChange,
  isOpen,
  onToggle,
  currentConversationId,
  onConversationSelect,
}: SidebarProps) {
  const { data: conversations } = trpc.chat.listConversations.useQuery();
  const utils = trpc.useUtils();
  const createConversation = trpc.chat.createConversation.useMutation({
    onSuccess: (data) => {
      utils.chat.listConversations.invalidate();
      onConversationSelect(data.id);
      onViewChange('chat');
    },
  });

  const handleNewChat = () => {
    createConversation.mutate({});
  };

  if (!isOpen) {
    return (
      <div className="w-12 bg-[var(--color-surface)] border-r border-white/10 flex flex-col items-center py-4">
        <button
          onClick={onToggle}
          className="p-2 rounded hover:bg-white/10 text-[var(--color-text)]"
          title="Open sidebar"
        >
          ☰
        </button>
      </div>
    );
  }

  return (
    <aside className="w-64 bg-[var(--color-surface)] border-r border-white/10 flex flex-col">
      {/* Header */}
      <div className="p-4 border-b border-white/10 flex items-center justify-between">
        <h1 className="text-lg font-semibold text-[var(--color-text)]">Lunaschal</h1>
        <button
          onClick={onToggle}
          className="p-1 rounded hover:bg-white/10 text-[var(--color-text-muted)]"
        >
          ✕
        </button>
      </div>

      {/* New Chat Button */}
      <div className="p-2">
        <button
          onClick={handleNewChat}
          disabled={createConversation.isPending}
          className="w-full py-2 px-3 rounded border border-white/20 text-[var(--color-text)] hover:bg-white/10 transition-colors disabled:opacity-50"
        >
          + New Chat
        </button>
      </div>

      {/* Navigation */}
      <nav className="p-2 border-b border-white/10">
        {navItems.map((item) => (
          <button
            key={item.view}
            onClick={() => onViewChange(item.view)}
            className={`w-full flex items-center gap-2 px-3 py-2 rounded text-left transition-colors ${
              currentView === item.view
                ? 'bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                : 'text-[var(--color-text)] hover:bg-white/10'
            }`}
          >
            <span>{item.icon}</span>
            <span>{item.label}</span>
          </button>
        ))}
      </nav>

      {/* Conversations List */}
      <div className="flex-1 overflow-y-auto p-2">
        <div className="text-xs text-[var(--color-text-muted)] px-3 py-2">Recent Chats</div>
        {conversations?.map((conv) => (
          <button
            key={conv.id}
            onClick={() => {
              onConversationSelect(conv.id);
              onViewChange('chat');
            }}
            className={`w-full text-left px-3 py-2 rounded text-sm truncate transition-colors ${
              currentConversationId === conv.id
                ? 'bg-white/10 text-[var(--color-text)]'
                : 'text-[var(--color-text-muted)] hover:bg-white/5'
            }`}
          >
            {conv.title || 'New Conversation'}
          </button>
        ))}
        {(!conversations || conversations.length === 0) && (
          <div className="text-sm text-[var(--color-text-muted)] px-3 py-2">No conversations yet</div>
        )}
      </div>
    </aside>
  );
}
