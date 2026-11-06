import { useState } from 'react';
import { ChatPanel } from './ChatPanel';
import { CodeTasks } from './CodeTasks';

/**
 * The Chat view: the conversation, and a Code page beside it.
 *
 * Was a two-tab layout — "Chat" and "Web Search" — until the delegate landed.
 * Searching is now something the model decides to do inside an ordinary
 * conversation, so making the user pick the mode up front meant asking them to
 * predict the answer before they had asked the question. Old web-search
 * conversations still render from their saved metadata (see `parseAgentMeta`);
 * there is simply no way to start a new one.
 *
 * Code is a second page for a different reason, and not a return of that
 * mode switch: it is not a way of asking the chat model something, it is a
 * different agent (the server's coding agent, backend/codeagent/) working on
 * tasks that take an hour and wait on approvals. Chat stays one conversation.
 */
export function Chat() {
  const [page, setPage] = useState<'chat' | 'code'>('chat');
  return (
    <div className="flex h-full flex-col">
      <div className="flex gap-1 px-4 pt-2">
        {(['chat', 'code'] as const).map(p => (
          <button
            key={p}
            onClick={() => setPage(p)}
            className={`px-3 py-1 text-sm rounded-lg ${
              page === p
                ? 'bg-white/10 text-[var(--color-text)]'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
            }`}
          >
            {p === 'chat' ? 'Chat' : 'Code'}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1">
        {page === 'chat' ? <ChatPanel /> : <CodeTasks />}
      </div>
    </div>
  );
}
