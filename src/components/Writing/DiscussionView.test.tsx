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
    return {
      status: 'idle',
      canTranscribe: true,
      error: '',
      start: recorderStart,
      stop: vi.fn(),
    };
  },
}));

vi.mock('../../hooks/api', () => ({
  api: {
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
    writing: {
      listNotes: vi.fn().mockResolvedValue([]),
      getNote: vi.fn(),
      updateNote: vi.fn().mockResolvedValue({ success: true }),
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
      resolveProposal: vi.fn(),
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

/** A /api/chat/stream response that is already finished. */
function closedStream() {
  return {
    ok: true,
    body: new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: [DONE]\n\n'));
        controller.close();
      },
    }),
  } as unknown as Response;
}

beforeEach(() => {
  vi.mocked(api.writing.summarizeDiscussion).mockReset();
  // The api mock is module-level, so call history survives into the next test
  // unless it is cleared — and several assertions here count calls.
  vi.mocked(api.chat.addMessage).mockClear();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(closedStream()));
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
  // Dictation used to land in the box and wait for a click, so a misheard
  // proper noun could be caught. The two-model STT cross-check is what made
  // that step cost more than it was worth.
  it('sends the transcript as a message the moment it arrives', async () => {
    renderWithProviders(<DiscussionView project={project} discussionId="d1" />);
    await screen.findByText('What if?');

    dictate('a masked stranger arrives at the gate');

    await waitFor(() =>
      expect(api.chat.addMessage).toHaveBeenCalledWith(
        'd1',
        expect.objectContaining({
          role: 'user',
          content: 'a masked stranger arrives at the gate',
        })
      )
    );
    const input = screen.getByPlaceholderText(
      'Discuss your story… (Enter to send)'
    );
    expect((input as HTMLTextAreaElement).value).toBe('');
  });

  it('sends what was typed and what was spoken as one message', async () => {
    renderWithProviders(<DiscussionView project={project} discussionId="d1" />);
    await screen.findByText('What if?');

    const input = screen.getByPlaceholderText(
      'Discuss your story… (Enter to send)'
    );
    fireEvent.change(input, { target: { value: 'what about this:' } });
    dictate('a masked stranger arrives at the gate');

    await waitFor(() =>
      expect(api.chat.addMessage).toHaveBeenCalledWith(
        'd1',
        expect.objectContaining({
          content: 'what about this: a masked stranger arrives at the gate',
        })
      )
    );
  });

  it('starts recording from the mic button', async () => {
    vi.mocked(api.settings.get).mockResolvedValueOnce({
      llamaUrl: 'http://localhost:8080',
    } as never);
    renderWithProviders(<DiscussionView project={project} discussionId="d1" />);
    await screen.findByText('What if?');

    fireEvent.click(screen.getByTitle('Speak to send'));
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

  it('leaves saving the reply to the server', async () => {
    // The run in backend/delegate/runs.py owns the assistant row, so the only
    // message this screen writes is the author's own.
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
    const fetches = vi.mocked(api.chat.getConversation).mock.calls.length;

    stream.push({ messageId: 'live1' });
    stream.push({ thinking: 'A clean exit keeps the mystery alive.' });
    stream.push({ content: 'She leaves to protect her sister.' });
    stream.close({ truncated: false, timedOut: false });

    // The finished row is re-read rather than written.
    await waitFor(() =>
      expect(
        vi.mocked(api.chat.getConversation).mock.calls.length
      ).toBeGreaterThan(fetches)
    );
    expect(api.chat.addMessage).toHaveBeenCalledTimes(1);
    expect(api.chat.addMessage).toHaveBeenCalledWith('d1', {
      role: 'user',
      content: 'why does she leave',
    });

    vi.unstubAllGlobals();
  });
});

describe('research tools', () => {
  it('sends only the transcript and the conversation', async () => {
    // The server reads conversations.writing_project_id and builds the
    // prompt, the tools and the project scope itself — none of them is the
    // client's to send, which is what lets another client take part.
    const stream = openStream();
    const fetchMock = vi.fn().mockResolvedValue(stream.response);
    vi.stubGlobal('fetch', fetchMock);

    renderWithProviders(<DiscussionView project={project} discussionId="d1" />);
    const input = await screen.findByPlaceholderText(
      'Discuss your story… (Enter to send)'
    );
    fireEvent.change(input, { target: { value: 'how did Tolkien do this' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.conversationId).toBe('d1');
    expect(body.messages.at(-1)).toEqual({
      role: 'user',
      content: 'how did Tolkien do this',
    });
    expect(body).not.toHaveProperty('systemPrompt');
    expect(body).not.toHaveProperty('toolset');
    expect(body).not.toHaveProperty('writingProjectId');

    stream.close({});
    vi.unstubAllGlobals();
  });

  it('shows a step as it happens', async () => {
    // Without this the turn is 30-90s of silence: the gathering happens before
    // a single token of the answer streams.
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));

    renderWithProviders(<DiscussionView project={project} discussionId="d1" />);
    const input = await screen.findByPlaceholderText(
      'Discuss your story… (Enter to send)'
    );
    fireEvent.change(input, { target: { value: 'what did the Romans eat' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(api.chat.addMessage).toHaveBeenCalledTimes(1));

    const step = { tool: 'local_knowledge_search', ok: true, count: 3 };
    stream.push(step);
    stream.push({ content: 'Mostly grain.' });
    await screen.findByText('Mostly grain.');
    expect(
      await screen.findByText(/Searched the offline library/i)
    ).not.toBeNull();

    stream.close({ steps: [step], sources: [] });
    vi.unstubAllGlobals();
  });

  it('shows a step before any content arrives', async () => {
    // The regression this pins: live steps used to render only alongside
    // streamed content, so during the 30-90s of gathering — the entire window
    // they exist to explain, and all of it before the first token — the panel
    // showed nothing. Deliberately asserts with no content pushed at all.
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));

    renderWithProviders(<DiscussionView project={project} discussionId="d1" />);
    const input = await screen.findByPlaceholderText(
      'Discuss your story… (Enter to send)'
    );
    fireEvent.change(input, { target: { value: 'what did the Romans eat' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(api.chat.addMessage).toHaveBeenCalledTimes(1));

    stream.push({ tool: 'local_knowledge_search', ok: true, count: 3 });

    expect(
      await screen.findByText(/Searched the offline library/i)
    ).not.toBeNull();
    expect(await screen.findByText(/1 step so far/i)).not.toBeNull();

    stream.close({});
    vi.unstubAllGlobals();
  });

  it('renders the trace saved on an earlier reply', async () => {
    vi.mocked(api.chat.getConversation).mockResolvedValueOnce({
      id: 'd5',
      title: 'Research talk',
      messages: [
        {
          id: 'm1',
          conversationId: 'd5',
          role: 'user',
          content: 'what did the Romans eat',
          metadata: null,
          createdAt: '',
        },
        {
          id: 'm2',
          conversationId: 'd5',
          role: 'assistant',
          content: 'Mostly grain.',
          metadata: JSON.stringify({
            steps: [{ tool: 'local_knowledge_search', ok: true, count: 3 }],
          }),
          createdAt: '',
        },
      ],
      createdAt: '',
      updatedAt: '',
    });
    renderWithProviders(<DiscussionView project={project} discussionId="d5" />);

    await screen.findByText('Mostly grain.');
    expect(
      await screen.findByText(/Searched the offline library/i)
    ).not.toBeNull();
  });
});

describe('notebook', () => {
  it('shows a proposed note as a card that can be accepted', async () => {
    vi.mocked(api.chat.getConversation).mockResolvedValueOnce({
      id: 'd6',
      title: 'Notebook talk',
      messages: [
        {
          id: 'm1',
          conversationId: 'd6',
          role: 'user',
          content: 'Let us make Mara a swimmer.',
          metadata: null,
          createdAt: '',
        },
        {
          id: 'm2',
          conversationId: 'd6',
          role: 'assistant',
          content: 'Good call — I staged it.',
          metadata: JSON.stringify({
            proposals: [
              {
                id: 'p1',
                kind: 'writing_note_update',
                status: 'pending',
                data: {
                  noteId: 'n1',
                  title: 'Mara',
                  mode: 'append',
                  content: 'She swims better than she walks.',
                },
              },
            ],
          }),
          createdAt: '',
        },
      ],
      createdAt: '',
      updatedAt: '',
    });
    vi.mocked(api.chat.resolveProposal).mockResolvedValueOnce({} as never);
    renderWithProviders(<DiscussionView project={project} discussionId="d6" />);

    expect(await screen.findByText('Update this note?')).not.toBeNull();
    expect(
      (screen.getByLabelText('Content') as HTMLTextAreaElement).value
    ).toBe('She swims better than she walks.');
    fireEvent.click(screen.getByText('Update Note'));

    await waitFor(() =>
      expect(api.chat.resolveProposal).toHaveBeenCalledWith(
        'm2',
        'p1',
        'accept',
        expect.objectContaining({ noteId: 'n1', mode: 'append' })
      )
    );
  });

  it('pins a note on the server rather than in this screen', async () => {
    vi.mocked(api.writing.listNotes).mockResolvedValueOnce([
      {
        id: 'n1',
        projectId: 'p1',
        title: 'Mara',
        docType: 'character',
        pinned: 0,
        createdAt: '',
        updatedAt: '',
      },
    ]);
    renderWithProviders(<DiscussionView project={project} discussionId="d1" />);

    fireEvent.click(await screen.findByText(/Pinned notes/));
    fireEvent.click(await screen.findByLabelText(/Mara/));

    await waitFor(() =>
      expect(api.writing.updateNote).toHaveBeenCalledWith('n1', {
        pinned: true,
      })
    );
  });
});
