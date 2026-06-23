import { useState } from 'react';
import { Sidebar } from './components/Sidebar';
import { Chat } from './components/Chat';
import { Journal } from './components/Journal';
import { Calendar } from './components/Calendar';
import { Flashcards } from './components/Flashcards';
import { Settings } from './components/Settings';
import { Editor } from './components/Editor';
import { SttPanel } from './components/Editor/SttPanel';

type View = 'chat' | 'journal' | 'calendar' | 'flashcards' | 'settings' | 'files';

export default function App() {
  const [currentView, setCurrentView] = useState<View>('chat');
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [pendingInsert, setPendingInsert] = useState<string | null>(null);

  const handleTranscribed = (text: string) => {
    if (currentView === 'files') {
      setPendingInsert(text);
    } else {
      navigator.clipboard.writeText(text).catch(() => {});
    }
  };

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
