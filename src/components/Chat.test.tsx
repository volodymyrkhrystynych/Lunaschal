// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../hooks/api';
import { Chat } from './Chat';

vi.mock('../hooks/api', () => ({
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

/** A /api/chat/stream response whose body stays open until the test closes it,
 * so we can inspect what the app does *while* a reply is generating. */
function openStream() {
  let push!: (chunk: string) => void;
  let close!: () => void;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const encode = new TextEncoder();
      push = chunk =>
        controller.enqueue(
          encode.encode(`data: ${JSON.stringify({ content: chunk })}\n\n`)
        );
      close = () => {
        controller.enqueue(encode.encode('data: [DONE]\n\n'));
        controller.close();
      };
    },
  });
  return { response: { ok: true, body } as unknown as Response, push, close };
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
  // jsdom has no layout, so the auto-scroll-to-bottom effect needs a stub.
  Element.prototype.scrollIntoView = vi.fn();
  vi.mocked(api.chat.today).mockResolvedValue(null);
  vi.mocked(api.settings.get).mockResolvedValue({
    ollamaUrl: 'http://localhost:11434',
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

describe('Chat send ordering', () => {
  it('does not classify until the reply has finished streaming', async () => {
    // Ollama serves one request at a time per model: a classify fired in
    // parallel with the reply wins the queue, and the user waits out a whole
    // second generation before their first token.
    const stream = openStream();
    const fetchMock = vi.fn().mockResolvedValue(stream.response);
    vi.stubGlobal('fetch', fetchMock);

    renderChat();
    const input = await screen.findByPlaceholderText('Type a message...');
    fireEvent.change(input, {
      target: { value: 'went for a long run this morning and felt great' },
    });
    fireEvent.keyDown(input, { key: 'Enter' });

    // The reply is underway...
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/chat/stream',
        expect.anything()
      )
    );
    stream.push('Nice, how far');
    await screen.findByText(/Nice, how far/);

    // ...and nothing else has been asked of the model yet.
    expect(api.chat.classify).not.toHaveBeenCalled();

    stream.close();
    await waitFor(() =>
      expect(api.chat.classify).toHaveBeenCalledWith(
        'went for a long run this morning and felt great'
      )
    );
  });

  it('still offers to save when the classifier comes back journal', async () => {
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));
    vi.mocked(api.chat.classify).mockResolvedValue({
      intent: 'journal',
      confidence: 0.9,
      journalEntry: {
        title: 'Morning run',
        content: 'Ran 10k.',
        tags: ['run'],
      },
    } as never);

    renderChat();
    const input = await screen.findByPlaceholderText('Type a message...');
    fireEvent.change(input, {
      target: { value: 'went for a long run this morning and felt great' },
    });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(api.chat.addMessage).toHaveBeenCalled());
    stream.push('Nice!');
    stream.close();

    expect(await screen.findByText(/Morning run/)).toBeTruthy();
  });
});
