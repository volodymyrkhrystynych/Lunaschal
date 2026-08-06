import { ChatPanel } from './ChatPanel';

/**
 * The Chat view.
 *
 * Was a two-tab layout — "Chat" and "Web Search" — until the delegate landed.
 * Searching is now something the model decides to do inside an ordinary
 * conversation, so making the user pick the mode up front meant asking them to
 * predict the answer before they had asked the question. Old web-search
 * conversations still render from their saved metadata (see `parseAgentMeta`);
 * there is simply no way to start a new one.
 */
export function Chat() {
  return <ChatPanel />;
}
