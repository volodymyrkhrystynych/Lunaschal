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
      saveJournal: vi.fn(),
      saveCalendar: vi.fn(),
      saveCalories: vi.fn(),
      saveTask: vi.fn(),
    },
    settings: { get: vi.fn() },
    learning: { generateForTopic: vi.fn(), generateFromNote: vi.fn() },
  },
}));

/** A /api/chat/stream response whose body stays open until the test closes it,
 * so live delegate steps and reasoning can be inspected mid-stream. */
function openStream() {
  let push!: (event: object) => void;
  let close!: (done: object) => void;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const encode = new TextEncoder();
      push = event =>
        controller.enqueue(encode.encode(`data: ${JSON.stringify(event)}\n\n`));
      close = done => {
        controller.enqueue(
          encode.encode(`data: ${JSON.stringify({ done: true, ...done })}\n\n`)
        );
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

async function send(text: string) {
  const input = await screen.findByPlaceholderText('Type a message...');
  fireEvent.change(input, { target: { value: text } });
  fireEvent.keyDown(input, { key: 'Enter' });
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  vi.mocked(api.chat.today).mockResolvedValue(null);
  vi.mocked(api.settings.get).mockResolvedValue({
    llamaUrl: 'http://localhost:8080',
  } as never);
  vi.mocked(api.chat.createConversation).mockResolvedValue({ id: 'c1' });
  vi.mocked(api.chat.addMessage).mockResolvedValue({ id: 'm1' });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('the chat has one mode', () => {
  it('goes straight to the message box with no tab to pick first', async () => {
    renderChat();
    await screen.findByPlaceholderText('Type a message...');
    // Searching is something the delegate decides to do mid-conversation, so
    // there is no longer a mode to choose before asking the question.
    expect(screen.queryByText('Web Search')).toBeNull();
  });

  it('posts to the single stream endpoint', async () => {
    const stream = openStream();
    const fetchMock = vi.fn().mockResolvedValue(stream.response);
    vi.stubGlobal('fetch', fetchMock);

    renderChat();
    await send('hello');

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/chat/stream',
        expect.anything()
      )
    );
  });
});

describe('delegate steps', () => {
  it('renders live steps collapsed, then persists them on the message', async () => {
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));

    renderChat();
    await send('who won the game last night');
    await waitFor(() => expect(api.chat.addMessage).toHaveBeenCalled());

    stream.push({
      tool: 'web_search',
      arg: 'game last night',
      ok: true,
      count: 2,
    });

    // Collapsed by default *while streaming*, not only once saved: a growing
    // list of steps used to push the reply down the page as it was being read.
    const summary = await screen.findByText(/1 step so far/);
    expect(summary.closest('details')).toHaveProperty('open', false);

    fireEvent.click(summary);
    await screen.findByText(/Searched the web for/);

    stream.push({ content: 'Team A won.' });
    await screen.findByText(/Team A won\./);
    stream.close({
      steps: [
        { tool: 'web_search', arg: 'game last night', ok: true, count: 2 },
      ],
      sources: [{ url: 'https://ex.com/score', title: 'Score' }],
      proposals: [],
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
  });

  it('labels a staged proposal as staged, never as saved', async () => {
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));

    renderChat();
    await send('remind me to call the dentist');
    await waitFor(() => expect(api.chat.addMessage).toHaveBeenCalled());

    stream.push({
      tool: 'propose_task',
      arg: 'to-do "Call the dentist"',
      ok: true,
    });
    fireEvent.click(await screen.findByText(/1 step so far/));

    // Nothing is written until the user clicks the confirm card below, and a
    // step list claiming otherwise is a lie they only catch by going to look.
    const label = await screen.findByText(/Staged a to-do/);
    expect(label.textContent).not.toMatch(/saved/i);
  });
});

describe('reasoning', () => {
  it('shows reasoning collapsed and keeps it out of the reply text', async () => {
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));

    renderChat();
    await send('why is the sky blue');
    await waitFor(() => expect(api.chat.addMessage).toHaveBeenCalled());

    stream.push({ thinking: 'Rayleigh scattering, probably.' });
    const summary = await screen.findByText('Reasoning');
    expect(summary.closest('details')).toHaveProperty('open', false);

    stream.push({ content: 'Because of Rayleigh scattering.' });
    stream.close({ steps: [], sources: [], proposals: [] });

    await waitFor(() =>
      expect(api.chat.addMessage).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({ content: 'Because of Rayleigh scattering.' })
      )
    );
    // Reasoning is the model talking to itself — never persisted, and never
    // folded into the saved reply.
    const saved = vi.mocked(api.chat.addMessage).mock.calls.at(-1)![1];
    expect(saved.content).not.toContain('Rayleigh scattering, probably.');
    expect(saved.metadata ?? '').not.toContain('probably');
  });
});
