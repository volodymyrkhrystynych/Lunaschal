import { useShortcuts } from '../shortcuts/ShortcutProvider';
import { useIsMobile } from '@/hooks/useMediaQuery';
import { useDesktopShell } from '@/hooks/useDesktopShell';

type View =
  | 'chat'
  | 'journal'
  | 'meetings'
  | 'calendar'
  | 'learning'
  | 'settings'
  | 'files'
  | 'notebook'
  | 'writing'
  | 'ideas'
  | 'food'
  | 'lifestyle'
  | 'fanfic'
  | 'newspapers'
  | 'paper'
  | 'email'
  | 'practice'
  | 'piano'
  | 'jobs';

interface SidebarProps {
  currentView: View;
  onViewChange: (view: View) => void;
  isOpen: boolean;
  onToggle: () => void;
  /** Each entry is a reason the Lifestyle tab needs a look today (missing
   *  selfie, low calories, …); the badge shows whenever this is non-empty. */
  lifestyleReasons?: string[];
  newspapersNeedAttention?: boolean;
}

export const navItems: {
  view: View;
  label: string;
  icon: string;
  desktopOnly?: boolean;
}[] = [
  { view: 'learning', label: 'Learning', icon: '🧠' },
  { view: 'practice', label: 'Practice', icon: '⌨️' },
  { view: 'piano', label: 'Piano', icon: '🎹', desktopOnly: true },
  { view: 'chat', label: 'Chat', icon: '💬' },
  { view: 'journal', label: 'Journal', icon: '📓' },
  // Lifestyle sits this high because it absorbed the Tasks tab: daily tasks and
  // the to-do list live inside it now, so it's a several-times-a-day view.
  { view: 'lifestyle', label: 'Lifestyle', icon: '🏋️' },
  { view: 'notebook', label: 'Notebook', icon: '🗒️' },
  { view: 'meetings', label: 'Meetings', icon: '🎙️' },
  { view: 'writing', label: 'Writing', icon: '✍️' },
  { view: 'ideas', label: 'Ideas', icon: '💡' },
  { view: 'calendar', label: 'Calendar', icon: '📅' },
  { view: 'food', label: 'Food', icon: '🍽️' },
  { view: 'fanfic', label: 'Library', icon: '📚' },
  { view: 'newspapers', label: 'Newspapers', icon: '📰' },
  { view: 'email', label: 'Email', icon: '📧' },
  // Next to Email on purpose: the two halves of a job search are the
  // applications you sent and the replies they get back.
  { view: 'jobs', label: 'Jobs', icon: '💼' },
  { view: 'paper', label: 'Paper', icon: '🖊️' },
  { view: 'files', label: 'Files', icon: '📁' },
  { view: 'settings', label: 'Settings', icon: '⚙️' },
];

export function Sidebar({
  currentView,
  onViewChange,
  isOpen,
  onToggle,
  lifestyleReasons,
  newspapersNeedAttention,
}: SidebarProps) {
  const { level } = useShortcuts();
  const isMobile = useIsMobile();
  const isDesktopShell = useDesktopShell();

  // On mobile, picking a view also closes the overlay drawer; on desktop the
  // sidebar stays pinned.
  const handleNav = (view: View) => {
    onViewChange(view);
    if (isMobile) onToggle();
  };

  const header = (
    <div className="p-4 border-b border-white/10 flex items-center justify-between">
      <h1 className="text-lg font-semibold text-[var(--color-text)]">
        Lunaschal
      </h1>
      <button
        onClick={onToggle}
        className="p-1 rounded hover:bg-white/10 text-[var(--color-text-muted)] min-h-[44px] min-w-[44px] md:min-h-0 md:min-w-0"
      >
        ✕
      </button>
    </div>
  );

  const nav = (
    <nav className="p-2 flex-1 overflow-y-auto">
      {navItems
        .filter(item => !item.desktopOnly || isDesktopShell)
        .map(item => (
          <button
            key={item.view}
            onClick={() => handleNav(item.view)}
            className={`w-full flex items-center gap-2 px-3 py-2 rounded text-left transition-colors min-h-[44px] md:min-h-0 ${
              currentView === item.view
                ? 'bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                : 'text-[var(--color-text)] hover:bg-white/10'
            } ${currentView === item.view && level === 0 ? 'ring-1 ring-[var(--color-primary)]' : ''}`}
          >
            <span>{item.icon}</span>
            <span>{item.label}</span>
            {item.view === 'lifestyle' &&
              lifestyleReasons &&
              lifestyleReasons.length > 0 && (
                <span
                  style={{ color: '#f0b429' }}
                  title={lifestyleReasons.join(' · ')}
                  aria-label={lifestyleReasons.join(' · ')}
                >
                  ❗
                </span>
              )}
            {item.view === 'newspapers' && newspapersNeedAttention && (
              <span
                style={{ color: '#f0b429' }}
                title="Today's front pages haven't all synced yet"
                aria-label="Today's front pages haven't all synced yet"
              >
                ❗
              </span>
            )}
          </button>
        ))}
    </nav>
  );

  // Mobile: off-canvas overlay drawer. The hamburger that opens it lives in the
  // App-level mobile header, so there's no collapsed rail here.
  if (isMobile) {
    if (!isOpen) return null;
    return (
      <div className="fixed inset-0 z-50 flex md:hidden">
        <div
          className="absolute inset-0 bg-black/50"
          onClick={onToggle}
          aria-hidden="true"
        />
        <aside className="relative w-64 max-w-[80%] h-full bg-[var(--color-surface)] border-r border-white/10 flex flex-col">
          {header}
          {nav}
        </aside>
      </div>
    );
  }

  // Desktop: unchanged inline rail (collapsed) / expanded sidebar.
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
      {header}
      {nav}
    </aside>
  );
}
