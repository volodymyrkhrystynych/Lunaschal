import { useState, useEffect } from 'react';
import { trpc } from './hooks/trpc';
import { Sidebar } from './components/Sidebar';
import { Chat } from './components/Chat';
import { Journal } from './components/Journal';
import { Calendar } from './components/Calendar';
import { Flashcards } from './components/Flashcards';
import { Settings } from './components/Settings';
import { Setup } from './components/Setup';
import { Login } from './components/Login';

type View = 'chat' | 'journal' | 'calendar' | 'flashcards' | 'settings';

export default function App() {
  const [currentView, setCurrentView] = useState<View>('chat');
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  const { data: isSetupComplete, isLoading } = trpc.settings.isSetupComplete.useQuery();

  if (isLoading) {
    return (
      <div className="h-screen flex items-center justify-center bg-[var(--color-bg)]">
        <div className="text-[var(--color-text-muted)]">Loading...</div>
      </div>
    );
  }

  if (isSetupComplete === false) {
    return <Setup />;
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
        return <Journal />;
      case 'calendar':
        return <Calendar />;
      case 'flashcards':
        return <Flashcards />;
      case 'settings':
        return <Settings />;
      default:
        return null;
    }
  };

  return (
    <div className="h-screen flex bg-[var(--color-bg)]">
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
  );
}
