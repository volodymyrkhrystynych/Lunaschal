import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Sidebar, navItems } from './components/Sidebar';
import { Chat } from './components/Chat';
import { Journal } from './components/Journal';
import { Calendar } from './components/Calendar';
import { Learning } from './components/Learning/Learning';
import { Settings } from './components/Settings';
import { Editor } from './components/Editor';
import { Notebook } from './components/Notebook/Notebook';
import { SttPanel } from './components/Editor/SttPanel';
import { OfflineIndicator } from './components/OfflineIndicator';
import { Login } from './components/Login';
import { Writing } from './components/Writing';
import { Ideas } from './components/Ideas';
import { Tasks } from './components/Tasks';
import { Food } from './components/Food/Food';
import { Lifestyle } from './components/Lifestyle/Lifestyle';
import { Fanfic } from './components/Fanfic/Fanfic';
import type { FicTarget } from './components/Fanfic/Fanfic';
import { Newspapers } from './components/Newspapers';
import { Paper } from './components/Paper/Paper';
import { Meetings } from './components/Meetings';
import { api } from './hooks/api';
import { useTodaySelfieStatus } from './hooks/useTodaySelfieStatus';
import { useTodayCaloriesStatus } from './hooks/useTodayCaloriesStatus';
import { useTodayNewspapersStatus } from './hooks/useTodayNewspapersStatus';
import { resolveAuthGate } from './lib/authGate';
import { ShortcutProvider } from './shortcuts/ShortcutProvider';
import { MOBILE_QUERY } from './lib/breakpoints';
import { getStoredView, setStoredView, type View } from './lib/viewPersistence';

export default function App() {
  const [currentView, setCurrentView] = useState<View>(
    () => getStoredView() ?? 'chat'
  );
  useEffect(() => {
    setStoredView(currentView);
  }, [currentView]);
  // Desktop starts with the sidebar pinned open; mobile starts with the drawer
  // closed. Read matchMedia synchronously so the drawer never flashes open on
  // a phone's first paint.
  const [sidebarOpen, setSidebarOpen] = useState(
    () => !window.matchMedia(MOBILE_QUERY).matches
  );
  const [pendingInsert, setPendingInsert] = useState<string | null>(null);
  const [ficTarget, setFicTarget] = useState<FicTarget | null>(null);
  const queryClient = useQueryClient();

  const {
    data: authStatus,
    isLoading: authLoading,
    isError: authError,
    refetch: refetchAuth,
  } = useQuery({
    queryKey: ['auth', 'status'],
    queryFn: api.auth.status,
    retry: false,
  });

  const authGate = resolveAuthGate({
    isLoading: authLoading,
    isError: authError,
    data: authStatus,
  });

  // Mounted regardless of currentView so the sidebar can flag a gap without
  // the user opening the Lifestyle/Newspapers tab; gated on 'app' so it
  // doesn't fire against a still-unauthenticated network-mode session.
  const missingSelfie = useTodaySelfieStatus(authGate === 'app');
  const lowCalories = useTodayCaloriesStatus(authGate === 'app');
  const lifestyleReasons = [
    missingSelfie && 'No selfie logged today',
    lowCalories && 'Under 1,500 calories logged today',
  ].filter((reason): reason is string => Boolean(reason));
  const newspapersNeedAttention = useTodayNewspapersStatus(authGate === 'app');

  const handleTranscribed = (text: string) => {
    if (currentView === 'files') {
      setPendingInsert(text);
    } else {
      navigator.clipboard.writeText(text).catch(() => {});
    }
  };

  if (authGate === 'loading') {
    return (
      <div className="h-dvh flex items-center justify-center bg-[var(--color-bg)]">
        <div className="text-[var(--color-text-muted)]">Loading…</div>
      </div>
    );
  }

  // Backend unreachable and no known-good session cached — don't mistake this
  // for a logout (that would bounce the user to Login on every wake-from-sleep
  // before Tailscale reconnects). Keep the session and retry; refetchOnReconnect
  // / refetchOnWindowFocus will also self-heal this once the backend answers.
  if (authGate === 'reconnecting') {
    return (
      <div className="h-dvh flex flex-col items-center justify-center gap-4 bg-[var(--color-bg)]">
        <div className="text-[var(--color-text-muted)]">
          Reconnecting to the server…
        </div>
        <button
          type="button"
          onClick={() => void refetchAuth()}
          className="px-4 py-2 bg-[var(--color-surface)] border border-white/10 rounded text-[var(--color-text)] hover:border-[var(--color-primary)] transition-colors"
        >
          Retry
        </button>
        <button
          type="button"
          onClick={() =>
            queryClient.setQueryData(['auth', 'status'], {
              authenticated: false,
              networkMode: true,
            })
          }
          className="text-sm text-[var(--color-text-muted)] underline hover:text-[var(--color-text)]"
        >
          Log in instead
        </button>
      </div>
    );
  }

  if (authGate === 'login') {
    return (
      <Login
        onSuccess={() =>
          queryClient.invalidateQueries({ queryKey: ['auth', 'status'] })
        }
      />
    );
  }

  const renderView = () => {
    switch (currentView) {
      case 'chat':
        return <Chat />;
      case 'journal':
        return (
          <Journal
            onOpenFic={target => {
              setFicTarget(target);
              setCurrentView('fanfic');
            }}
          />
        );
      case 'calendar':
        return <Calendar />;
      case 'learning':
        return <Learning />;
      case 'settings':
        return <Settings />;
      case 'files':
        return (
          <Editor
            pendingInsert={pendingInsert}
            onInsertDone={() => setPendingInsert(null)}
          />
        );
      case 'notebook':
        return <Notebook />;
      case 'writing':
        return <Writing />;
      case 'ideas':
        return <Ideas />;
      case 'tasks':
        return <Tasks />;
      case 'food':
        return <Food />;
      case 'lifestyle':
        return <Lifestyle />;
      case 'fanfic':
        return (
          <Fanfic
            target={ficTarget}
            onTargetConsumed={() => setFicTarget(null)}
          />
        );
      case 'newspapers':
        return <Newspapers />;
      case 'paper':
        return <Paper />;
      case 'meetings':
        return <Meetings />;
      default:
        return null;
    }
  };

  return (
    <ShortcutProvider
      currentView={currentView}
      onViewChange={setCurrentView}
      onToggleSidebar={() => setSidebarOpen(o => !o)}
    >
      <div className="h-dvh flex flex-col bg-[var(--color-bg)]">
        <header className="md:hidden h-11 shrink-0 flex items-center gap-2 px-2 border-b border-white/10 bg-[var(--color-surface)]">
          <button
            type="button"
            onClick={() => setSidebarOpen(true)}
            className="p-2 min-h-[44px] min-w-[44px] flex items-center justify-center rounded hover:bg-white/10 text-[var(--color-text)]"
            aria-label="Open menu"
          >
            ☰
          </button>
          <span className="font-semibold text-[var(--color-text)]">
            {navItems.find(i => i.view === currentView)?.label ?? 'Lunaschal'}
          </span>
        </header>
        <div className="flex flex-1 overflow-hidden">
          <Sidebar
            currentView={currentView}
            onViewChange={setCurrentView}
            isOpen={sidebarOpen}
            onToggle={() => setSidebarOpen(!sidebarOpen)}
            lifestyleReasons={lifestyleReasons}
            newspapersNeedAttention={newspapersNeedAttention}
          />
          <main className="flex-1 flex flex-col overflow-hidden">
            {renderView()}
          </main>
        </div>
        <OfflineIndicator />
        <SttPanel
          onTranscribed={handleTranscribed}
          onMeetingUploaded={() => setCurrentView('meetings')}
        />
      </div>
    </ShortcutProvider>
  );
}
