// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ShortcutProvider } from '../../shortcuts/ShortcutProvider';
import { api, type WritingProject } from '../../hooks/api';
import { DiscussionView } from './DiscussionView';

// Only the transcript matters here — the microphone plumbing has its own
// test (useRecorder.test.tsx), same split as ChatComposer.test.tsx.
let dictate: (text: string) => void = () => {};
const recorderStart = vi.fn();
vi.mock('../../hooks/useRecorder', () => ({
  useRecorder: (onTranscript: (text: string) => void) => {
    dictate = onTranscript;
    return { status: 'idle', error: '', start: recorderStart, stop: vi.fn() };
  },
}));

vi.mock('../../hooks/api', () => ({
  api: {
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
    writing: {
      listNotes: vi.fn().mockResolvedValue([]),
      getNote: vi.fn(),
      summarizeDiscussion: vi.fn(),
    },
    chat: {
      getConversation: vi.fn().mockResolvedValue({
        id: 'd1',
        title: 'Plot talk',
        messages: [
          {
            id: 'm1',
            conversationId: 'd1',
            role: 'user',
            content: 'What if?',
            metadata: null,
            createdAt: '',
          },
          {
            id: 'm2',
            conversationId: 'd1',
            role: 'assistant',
            content: 'Then this.',
            metadata: null,
            createdAt: '',
          },
        ],
      }),
      updateTitle: vi.fn(),
      deleteConversation: vi.fn(),
      addMessage: vi.fn(),
    },
  },
}));

const project: WritingProject = {
  id: 'p1',
  title: 'My Story',
  description: null,
  createdAt: '',
  updatedAt: '',
};

beforeEach(() => {
  vi.mocked(api.writing.summarizeDiscussion).mockReset();
});

function renderWithProviders(children: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="writing" onViewChange={() => {}}>
        {children}
      </ShortcutProvider>
    </QueryClientProvider>
  );
}

/** A /api/chat/stream response whose body stays open until the test closes
 * it, so reasoning can be inspected mid-stream before the reply is
 * persisted. Same shape as Chat.test.tsx's helper of the same name. */
function openStream() {
  let push!: (event: object) => void;
  let close!: (done?: object) => void;
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

describe('assistant markdown rendering', () => {
  it('renders assistant markdown as HTML but keeps user messages literal', async () => {
    vi.mocked(api.chat.getConversation).mockResolvedValueOnce({
      id: 'd3',
      title: 'Markdown talk',
      messages: [
        {
          id: 'm1',
          conversationId: 'd3',
          role: 'user',
          content: 'use **plain** please',
          metadata: null,
          createdAt: '',
        },
        {
          id: 'm2',
          conversationId: 'd3',
          role: 'assistant',
          content: 'A **bold** idea',
          metadata: null,
          createdAt: '',
        },
      ],
      createdAt: '',
      updatedAt: '',
    });
    renderWithProviders(<DiscussionView project={project} discussionId="d3" />);

    const bold = await screen.findByText('bold');
    expect(bold.tagName).toBe('STRONG');
    // User text is not interpreted as markdown
    expect(screen.getByText('use **plain** please')).not.toBeNull();
  });
});

describe('discussion summarize', () => {
  it('saves a summary note and offers to open it', async () => {
    vi.mocked(api.writing.summarizeDiscussion).mockResolvedValue({
      id: 'n9',
      projectId: 'p1',
      title: 'Villain twist decision',
      content: '- twist',
      docType: 'note',
      createdAt: '',
      updatedAt: '',
    });
    const onNoteCreated = vi.fn();
    renderWithProviders(
      <DiscussionView
        project={project}
        discussionId="d1"
        onNoteCreated={onNoteCreated}
      />
    );

    await screen.findByText('What if?'); // wait for the transcript so the button enables
    fireEvent.click(screen.getByText('Summarize'));

    expect(await screen.findByText(/Summary saved to Notes/)).not.toBeNull();
    expect(api.writing.summarizeDiscussion).toHaveBeenCalledWith('d1');

    fireEvent.click(screen.getByText('Open note'));
    expect(onNoteCreated).toHaveBeenCalledWith('n9');
  });

  it('shows the server error when summarization fails', async () => {
    vi.mocked(api.writing.summarizeDiscussion).mockRejectedValue(
      new Error('AI provider not configured')
    );
    renderWithProviders(<DiscussionView project={project} discussionId="d1" />);

    await screen.findByText('What if?');
    fireEvent.click(screen.getByText('Summarize'));

    expect(
      await screen.findByText('AI provider not configured')
    ).not.toBeNull();
    expect(screen.queryByText(/Summary saved to Notes/)).toBeNull();
  });

  it('disables the button while the discussion has no messages', async () => {
    vi.mocked(api.chat.getConversation).mockResolvedValueOnce({
      id: 'd2',
      title: 'Empty',
      messages: [],
      createdAt: '',
      updatedAt: '',
    });
    renderWithProviders(<DiscussionView project={project} discussionId="d2" />);

    const button = await screen.findByText('Summarize');
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('speech to text', () => {
  it('lands the transcript in the box instead of sending it', async () => {
    renderWithProviders(<DiscussionView project={project} discussionId="d1" />);
    await screen.findByText('What if?');

    dictate('a masked stranger arrives at the gate');

    const input = screen.getByPlaceholderText(
      'Discuss your story… (Enter to send)'
    );
    await waitFor(() =>
      expect((input as HTMLTextAreaElement).value).toBe(
        'a masked stranger arrives at the gate'
      )
    );
    expect(api.chat.addMessage).not.toHaveBeenCalled();
  });

  it('starts recording from the mic button', async () => {
    vi.mocked(api.settings.get).mockResolvedValueOnce({
      llamaUrl: 'http://localhost:8080',
    } as never);
    renderWithProviders(<DiscussionView project={project} discussionId="d1" />);
    await screen.findByText('What if?');

    fireEvent.click(screen.getByTitle('Speak to add to the box'));
    expect(recorderStart).toHaveBeenCalled();
  });
});

describe('reasoning', () => {
  it('shows reasoning saved on an earlier reply, collapsed', async () => {
    vi.mocked(api.chat.getConversation).mockResolvedValueOnce({
      id: 'd4',
      title: 'Reasoning talk',
      messages: [
        {
          id: 'm1',
          conversationId: 'd4',
          role: 'user',
          content: 'why does the villain betray the hero',
          metadata: null,
          createdAt: '',
        },
        {
          id: 'm2',
          conversationId: 'd4',
          role: 'assistant',
          content: 'Because it raises the stakes.',
          metadata: JSON.stringify({
            thinking: 'Betrayal works best from a trusted ally.',
          }),
          createdAt: '',
        },
      ],
      createdAt: '',
      updatedAt: '',
    });
    renderWithProviders(<DiscussionView project={project} discussionId="d4" />);

    await screen.findByText('Because it raises the stakes.');
    const summary = await screen.findByText('Reasoning');
    expect(summary.closest('details')).toHaveProperty('open', false);

    fireEvent.click(summary);
    expect(
      await screen.findByText('Betrayal works best from a trusted ally.')
    ).not.toBeNull();
  });

  it('persists the reasoning trace onto the saved assistant message', async () => {
    vi.mocked(api.settings.get).mockResolvedValueOnce({
      llamaUrl: 'http://localhost:8080',
    } as never);
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));

    renderWithProviders(<DiscussionView project={project} discussionId="d1" />);
    const input = await screen.findByPlaceholderText(
      'Discuss your story… (Enter to send)'
    );
    fireEvent.change(input, { target: { value: 'why does she leave' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(api.chat.addMessage).toHaveBeenCalledTimes(1));

    stream.push({ thinking: 'A clean exit keeps the mystery alive.' });
    stream.push({ content: 'She leaves to protect her sister.' });
    stream.close({ truncated: false, timedOut: false });

    await waitFor(() =>
      expect(api.chat.addMessage).toHaveBeenCalledWith('d1', {
        role: 'assistant',
        content: 'She leaves to protect her sister.',
        metadata: JSON.stringify({
          thinking: 'A clean exit keeps the mystery alive.',
          truncated: false,
          timedOut: false,
        }),
      })
    );

    vi.unstubAllGlobals();
  });
});
