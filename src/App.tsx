import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Sidebar } from './components/Sidebar';
import { Chat } from './components/Chat';
import { Journal } from './components/Journal';
import { Calendar } from './components/Calendar';
import { Flashcards } from './components/Flashcards';
import { Settings } from './components/Settings';
import { Editor } from './components/Editor';
import { SttPanel } from './components/Editor/SttPanel';
import { Login } from './components/Login';
import { Writing } from './components/Writing';
import { api } from './hooks/api';

type View = 'chat' | 'journal' | 'calendar' | 'flashcards' | 'settings' | 'files' | 'writing';

export default function App() {
  const [currentView, setCurrentView] = useState<View>('chat');
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [pendingInsert, setPendingInsert] = useState<string | null>(null);
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
    return <Login onSuccess={() => queryClient.invalidateQueries({ queryKey: ['auth', 'status'] })} />;
  }

  const renderView = () => {
    switch (currentView) {
      case 'chat':
        return <Chat conversationId={currentConversationId} onConversationChange={setCurrentConversationId} />;
      case 'journal':
        return <Journal />;
      case 'calendar':
        return <Calendar />;
      case 'flashcards':
        return <Flashcards />;
      case 'settings':
        return <Settings />;
      case 'files':
        return <Editor pendingInsert={pendingInsert} onInsertDone={() => setPendingInsert(null)} />;
      case 'writing':
        return <Writing />;
      default:
        return null;
    }
  };

  return (
    <div className="h-screen flex flex-col bg-[var(--color-bg)]">
      <div className="flex flex-1 overflow-hidden">
        <Sidebar
          currentView={currentView}
          onViewChange={setCurrentView}
          isOpen={sidebarOpen}
          onToggle={() => setSidebarOpen(!sidebarOpen)}
          currentConversationId={currentConversationId}
          onConversationSelect={setCurrentConversationId}
        />
        <main className="flex-1 flex flex-col overflow-hidden">{renderView()}</main>
      </div>
      <SttPanel onTranscribed={handleTranscribed} />
    </div>
  );
}
