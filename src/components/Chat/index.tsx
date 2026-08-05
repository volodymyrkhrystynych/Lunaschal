import { useState } from 'react';
import type { ChatMode } from '../../hooks/api';
import { ChatPanel } from './ChatPanel';

const TABS: { mode: ChatMode; label: string }[] = [
  { mode: 'chat', label: 'Chat' },
  { mode: 'websearch', label: 'Web Search' },
];

export function Chat() {
  // Always starts on the regular chat tab — not persisted across view
  // switches or reloads.
  const [activeTab, setActiveTab] = useState<ChatMode>('chat');

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex gap-1 px-4 pt-2 border-b border-white/10">
        {TABS.map(tab => (
          <button
            key={tab.mode}
            onClick={() => setActiveTab(tab.mode)}
            className={`px-3 py-1.5 text-sm rounded-t transition-colors ${
              activeTab === tab.mode
                ? 'text-[var(--color-primary)] border-b-2 border-[var(--color-primary)]'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {TABS.map(tab => (
        <div
          key={tab.mode}
          data-tab-panel={tab.mode}
          className="flex-1 flex flex-col overflow-hidden"
          style={activeTab === tab.mode ? undefined : { display: 'none' }}
        >
          <ChatPanel mode={tab.mode} />
        </div>
      ))}
    </div>
  );
}
