import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Sidebar } from './components/Sidebar';
import { Chat } from './components/Chat';
import { Journal } from './components/Journal';
import { Calendar } from './components/Calendar';
import { Learning } from './components/Learning/Learning';
import { Settings } from './components/Settings';
import { Editor } from './components/Editor';
import { SttPanel } from './components/Editor/SttPanel';
import { Login } from './components/Login';
import { Writing } from './components/Writing';
import { Tasks } from './components/Tasks';
import { Cookbook } from './components/Cookbook';
import { Fanfic } from './components/Fanfic/Fanfic';
import type { FicTarget } from './components/Fanfic/Fanfic';
import { Newspapers } from './components/Newspapers';
import { Meetings } from './components/Meetings';
import { api } from './hooks/api';
import { ShortcutProvider } from './shortcuts/ShortcutProvider';

type View =
  | 'chat'
  | 'journal'
  | 'meetings'
  | 'calendar'
  | 'learning'
  | 'settings'
  | 'files'
  | 'writing'
  | 'tasks'
  | 'cookbook'
  | 'fanfic'
  | 'newspapers';

export default function App() {
  const [currentView, setCurrentView] = useState<View>('chat');
  const [currentConversationId, setCurrentConversationId] = useState<
    string | null
  >(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [pendingInsert, setPendingInsert] = useState<string | null>(null);
  const [ficTarget, setFicTarget] = useState<FicTarget | null>(null);
  const queryClient = useQueryClient();

  const { data: authStatus, isLoading: authLoading } = useQuery({
    queryKey: ['auth', 'status'],
    queryFn: api.auth.status,
    retry: false,
  });

  const handleTranscribed = (text: string) => {
    if (currentView === 'files') {
      setPendingInsert(text);
    } else {
      navigator.clipboard.writeText(text).catch(() => {});
    }
  };

  if (authLoading) {
    return (
      <div className="h-screen flex items-center justify-center bg-[var(--color-bg)]">
        <div className="text-[var(--color-text-muted)]">Loading…</div>
      </div>
    );
  }

  if (!authStatus?.authenticated) {
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
        return (
          <Chat
            conversationId={currentConversationId}
            onConversationChange={setCurrentConversationId}
          />
        );
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
      case 'writing':
        return <Writing />;
      case 'tasks':
        return <Tasks />;
      case 'cookbook':
        return <Cookbook />;
      case 'fanfic':
        return (
          <Fanfic
            target={ficTarget}
            onTargetConsumed={() => setFicTarget(null)}
          />
        );
      case 'newspapers':
        return <Newspapers />;
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
      <div className="h-screen flex flex-col bg-[var(--color-bg)]">
        <div className="flex flex-1 overflow-hidden">
          <Sidebar
            currentView={currentView}
            onViewChange={setCurrentView}
            isOpen={sidebarOpen}
            onToggle={() => setSidebarOpen(!sidebarOpen)}
          />
          <main className="flex-1 flex flex-col overflow-hidden">
            {renderView()}
          </main>
        </div>
        <SttPanel
          onTranscribed={handleTranscribed}
          onMeetingUploaded={() => setCurrentView('meetings')}
        />
      </div>
    </ShortcutProvider>
  );
}
