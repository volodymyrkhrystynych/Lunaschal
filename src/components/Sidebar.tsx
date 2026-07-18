import { useShortcuts } from '../shortcuts/ShortcutProvider';

type View = 'chat' | 'journal' | 'meetings' | 'calendar' | 'learning' | 'settings' | 'files' | 'writing' | 'tasks' | 'cookbook' | 'fanfic' | 'newspapers';

interface SidebarProps {
  currentView: View;
  onViewChange: (view: View) => void;
  isOpen: boolean;
  onToggle: () => void;
}

export const navItems: { view: View; label: string; icon: string }[] = [
  { view: 'chat', label: 'Chat', icon: '💬' },
  { view: 'tasks', label: 'Tasks', icon: '✅' },
  { view: 'journal', label: 'Journal', icon: '📓' },
  { view: 'meetings', label: 'Meetings', icon: '🎙️' },
  { view: 'writing', label: 'Writing', icon: '✍️' },
  { view: 'calendar', label: 'Calendar', icon: '📅' },
  { view: 'learning', label: 'Learning', icon: '🧠' },
  { view: 'cookbook', label: 'Cookbook', icon: '🍳' },
  { view: 'fanfic', label: 'Library', icon: '📚' },
  { view: 'newspapers', label: 'Newspapers', icon: '📰' },
  { view: 'files', label: 'Files', icon: '📁' },
  { view: 'settings', label: 'Settings', icon: '⚙️' },
];

export function Sidebar({ currentView, onViewChange, isOpen, onToggle }: SidebarProps) {
  const { level } = useShortcuts();

  if (!isOpen) {
    return (
      <div className="w-12 bg-[var(--color-surface)] border-r border-white/10 flex flex-col items-center py-4">
        <button onClick={onToggle} className="p-2 rounded hover:bg-white/10 text-[var(--color-text)]" title="Open sidebar">☰</button>
      </div>
    );
  }

  return (
    <aside className="w-64 bg-[var(--color-surface)] border-r border-white/10 flex flex-col">
      <div className="p-4 border-b border-white/10 flex items-center justify-between">
        <h1 className="text-lg font-semibold text-[var(--color-text)]">Lunaschal</h1>
        <button onClick={onToggle} className="p-1 rounded hover:bg-white/10 text-[var(--color-text-muted)]">✕</button>
      </div>

      <nav className="p-2 flex-1 overflow-y-auto">
        {navItems.map((item) => (
          <button key={item.view} onClick={() => onViewChange(item.view)}
            className={`w-full flex items-center gap-2 px-3 py-2 rounded text-left transition-colors ${
              currentView === item.view
                ? 'bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                : 'text-[var(--color-text)] hover:bg-white/10'
            } ${currentView === item.view && level === 0 ? 'ring-1 ring-[var(--color-primary)]' : ''}`}>
            <span>{item.icon}</span>
            <span>{item.label}</span>
          </button>
        ))}
      </nav>
    </aside>
  );
}
