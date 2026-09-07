import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Sidebar, navItems } from './components/Sidebar';
import { Chat } from './components/Chat';
import { Journal } from './components/Journal';
import { Calendar } from './components/Calendar';
import { Learning } from './components/Learning/Learning';
import { Practice } from './components/Practice';
import { Settings } from './components/Settings';
import { Editor } from './components/Editor';
import { Notebook } from './components/Notebook/Notebook';
import { SttPanel } from './components/Editor/SttPanel';
import { InferencePausedBanner } from './components/InferencePausedBanner';
import { OfflineIndicator } from './components/OfflineIndicator';
import { Login } from './components/Login';
import { Writing } from './components/Writing';
import { Ideas } from './components/Ideas';
import { Food } from './components/Food/Food';
import { Lifestyle } from './components/Lifestyle/Lifestyle';
import { Fanfic } from './components/Fanfic/Fanfic';
import type { FicTarget } from './components/Fanfic/Fanfic';
import { Newspapers } from './components/Newspapers';
import { Torrent } from './components/Torrent/TorrentList';
import { Email } from './components/Email';
import { Jobs } from './components/Jobs';
import { Paper } from './components/Paper/Paper';
import { Meetings } from './components/Meetings';
import { Piano } from './components/Piano';
import { Knowledge } from './components/Knowledge/Knowledge';
import { Study } from './components/Study/Study';
import { api } from './hooks/api';
import { useTodaySelfieStatus } from './hooks/useTodaySelfieStatus';
import { useTodayCaloriesStatus } from './hooks/useTodayCaloriesStatus';
import { useTodayNewspapersStatus } from './hooks/useTodayNewspapersStatus';
import { useTorrentStatus } from './hooks/useTorrentStatus';
import { resolveAuthGate } from './lib/authGate';
import { ShortcutProvider } from './shortcuts/ShortcutProvider';
import { MOBILE_QUERY } from './lib/breakpoints';
import { getStoredView, setStoredView, type View } from './lib/viewPersistence';
import { visibleNavItems } from './lib/navVisibility';
import { useDesktopShell } from './hooks/useDesktopShell';
import {
  ImmersiveProvider,
  useBottomBarHidden,
  useImmersive,
} from './components/ImmersiveContext';
import { recordBrowserSignal } from './lib/browserDiagnostics';

/**
 * The shell, wrapped so a view can ask for it to get out of the way. Split in
 * two only because the provider has to sit *above* the component that reads it.
 */
export default function App() {
  return (
    <ImmersiveProvider>
      <AppShell />
    </ImmersiveProvider>
  );
}

function AppShell() {
  useEffect(() => {
    recordBrowserSignal('shell-mount');
    return () => recordBrowserSignal('shell-unmount');
  }, []);
  const isDesktopShell = useDesktopShell();
  // Set by the Paper editor on a tablet: no header, no sidebar, no bottom bar,
  // so the page is the screen and its own Back button is the way out.
  const immersive = useImmersive();
  const bottomBarHidden = useBottomBarHidden();
  const [currentView, setCurrentView] = useState<View>(
    () => getStoredView() ?? 'chat'
  );
  useEffect(() => {
    setStoredView(currentView);
  }, [currentView]);

  const availableViews = visibleNavItems(navItems, { isDesktopShell }).map(
    item => item.view
  );

  // A gated view the last session left behind renders nothing at all on a
  // device that can't show it — a phone whose stored view is 'piano' came up
  // to a blank <main> with no way back except the sidebar. Fall back to the
  // default rather than leaving the shell empty. Study is no longer one of
  // these: it exists on every device and narrows itself instead.
  const viewAvailable = availableViews.includes(currentView);
  useEffect(() => {
    if (!viewAvailable) setCurrentView('chat');
  }, [viewAvailable]);
  // Desktop starts with the sidebar pinned open; mobile starts with the drawer
  // closed. Read matchMedia synchronously so the drawer never flashes open on
  // a phone's first paint.
  const [sidebarOpen, setSidebarOpen] = useState(
    () => !window.matchMedia(MOBILE_QUERY).matches
  );
  const [pendingInsert, setPendingInsert] = useState<string | null>(null);
  const [ficTarget, setFicTarget] = useState<FicTarget | null>(null);
  // The two halves of a dictated idea point at each other across tabs: the
  // journal entry holds the recording, the idea holds the thought.
  const [ideaTarget, setIdeaTarget] = useState<{ ideaId: string } | null>(null);
  const [journalTarget, setJournalTarget] = useState<{
    entryId: string;
  } | null>(null);
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
  useEffect(() => recordBrowserSignal('auth-gate', authGate), [authGate]);
  useEffect(() => recordBrowserSignal('view', currentView), [currentView]);

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
  const torrentsNeedAttention = useTorrentStatus(authGate === 'app');

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
            onOpenIdea={target => {
              setIdeaTarget(target);
              setCurrentView('ideas');
            }}
            target={journalTarget}
            onTargetConsumed={() => setJournalTarget(null)}
          />
        );
      case 'calendar':
        return <Calendar />;
      case 'learning':
        return <Learning />;
      case 'practice':
        return <Practice />;
      case 'piano':
        return isDesktopShell ? <Piano /> : null;
      case 'study':
        return <Study />;
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
        return (
          <Ideas
            target={ideaTarget}
            onTargetConsumed={() => setIdeaTarget(null)}
            onOpenEntry={entryId => {
              setJournalTarget({ entryId });
              setCurrentView('journal');
            }}
          />
        );
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
      case 'knowledge':
        return <Knowledge />;
      case 'newspapers':
        return <Newspapers />;
      case 'torrent':
        return <Torrent />;
      case 'email':
        return <Email />;
      case 'jobs':
        return <Jobs />;
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
      availableViews={availableViews}
    >
      <div className="h-dvh flex flex-col bg-[var(--color-bg)]">
        {!immersive && (
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
        )}
        <div className="flex flex-1 overflow-hidden">
          {!immersive && (
            <Sidebar
              currentView={currentView}
              onViewChange={setCurrentView}
              isOpen={sidebarOpen}
              onToggle={() => setSidebarOpen(!sidebarOpen)}
              lifestyleReasons={lifestyleReasons}
              newspapersNeedAttention={newspapersNeedAttention}
              torrentsNeedAttention={torrentsNeedAttention}
            />
          )}
          <main className="flex-1 flex flex-col overflow-hidden">
            {renderView()}
          </main>
        </div>
        {/* Settings owns the switch, but the consequence is felt in every
         * view — so the state is said here rather than only where it is set. */}
        <InferencePausedBanner />
        {/* The one piece of chrome immersive mode keeps: whether the backend is
         * reachable is exactly what a page being drawn on offline needs to say. */}
        <OfflineIndicator />
        {!bottomBarHidden && (
          <SttPanel
            onTranscribed={handleTranscribed}
            onMeetingUploaded={() => setCurrentView('meetings')}
          />
        )}
      </div>
    </ShortcutProvider>
  );
}
