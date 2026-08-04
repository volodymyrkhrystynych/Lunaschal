// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { Chat } from './index';

vi.mock('../../hooks/api', () => ({
  api: {
    chat: {
      today: vi.fn(),
      createConversation: vi.fn(),
      addMessage: vi.fn(),
      classify: vi.fn(),
      saveJournal: vi.fn(),
      saveCalendar: vi.fn(),
    },
    settings: { get: vi.fn() },
    learning: { generateForTopic: vi.fn() },
  },
}));

/** A /api/chat/websearch/stream response whose body stays open until the test
 * closes it, so the live tool-step rendering can be inspected mid-stream. */
function openWebSearchStream() {
  let pushStep!: (step: object) => void;
  let pushContent!: (chunk: string) => void;
  let close!: (done: object) => void;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const encode = new TextEncoder();
      pushStep = step =>
        controller.enqueue(encode.encode(`data: ${JSON.stringify(step)}\n\n`));
      pushContent = chunk =>
        controller.enqueue(
          encode.encode(`data: ${JSON.stringify({ content: chunk })}\n\n`)
        );
      close = done => {
        controller.enqueue(
          encode.encode(`data: ${JSON.stringify({ done: true, ...done })}\n\n`)
        );
        controller.enqueue(encode.encode('data: [DONE]\n\n'));
        controller.close();
      };
    },
  });
  return {
    response: { ok: true, body } as unknown as Response,
    pushStep,
    pushContent,
    close,
  };
}

const renderChat = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Chat />
    </QueryClientProvider>
  );
};

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  vi.mocked(api.chat.today).mockResolvedValue(null);
  vi.mocked(api.settings.get).mockResolvedValue({
    llamaUrl: 'http://localhost:8080',
  } as never);
  vi.mocked(api.chat.createConversation).mockResolvedValue({ id: 'c1' });
  vi.mocked(api.chat.addMessage).mockResolvedValue({ id: 'm1' });
  vi.mocked(api.chat.classify).mockResolvedValue({
    intent: 'conversation',
    confidence: 1,
  } as never);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Chat tab switching', () => {
  it('defaults to the regular Chat tab', async () => {
    renderChat();
    await screen.findByPlaceholderText('Type a message...');
    expect(api.chat.today).toHaveBeenCalledWith('chat');
  });

  it('switches to the Web Search tab and queries its own conversation', async () => {
    renderChat();
    await screen.findByPlaceholderText('Type a message...');

    fireEvent.click(screen.getByText('Web Search'));

    await screen.findByPlaceholderText('Ask something to look up...');
    expect(api.chat.today).toHaveBeenCalledWith('websearch');
  });

  it("switching tabs does not carry over the other tab's conversation", async () => {
    vi.mocked(api.chat.today).mockImplementation(async mode =>
      mode === 'chat'
        ? {
            id: 'chat-conv',
            title: null,
            mode: 'chat',
            createdAt: '',
            updatedAt: '',
            messages: [
              {
                id: 'm1',
                conversationId: 'chat-conv',
                role: 'user',
                content: 'hello from regular chat',
                metadata: null,
                createdAt: '',
              },
            ],
          }
        : null
    );

    renderChat();
    await screen.findByText('hello from regular chat');

    fireEvent.click(screen.getByText('Web Search'));
    await screen.findByPlaceholderText('Ask something to look up...');
    expect(screen.queryByText('hello from regular chat')).toBeNull();
  });
});

describe('Web Search tab streaming', () => {
  it('streams tool steps live, then persists them on the assistant message', async () => {
    const stream = openWebSearchStream();
    const fetchMock = vi.fn().mockResolvedValue(stream.response);
    vi.stubGlobal('fetch', fetchMock);

    renderChat();
    await screen.findByPlaceholderText('Type a message...');
    fireEvent.click(screen.getByText('Web Search'));
    const input = await screen.findByPlaceholderText(
      'Ask something to look up...'
    );

    fireEvent.change(input, {
      target: { value: 'who won the game last night' },
    });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/chat/websearch/stream',
        expect.anything()
      )
    );

    stream.pushStep({
      tool: 'web_search',
      arg: 'game last night',
      ok: true,
      count: 2,
    });
    await screen.findByText(/Searched the web for/);

    stream.pushContent('Team A won.');
    await screen.findByText(/Team A won\./);

    stream.close({
      sources: [{ url: 'https://ex.com/score', title: 'Score' }],
    });

    await waitFor(() =>
      expect(api.chat.addMessage).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({
          role: 'assistant',
          content: 'Team A won.',
          metadata: expect.stringContaining('web_search'),
        })
      )
    );
    // A web-search lookup isn't journal/calendar material — no classify call.
    expect(api.chat.classify).not.toHaveBeenCalled();
  });
});
