// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { ChatPanel } from './ChatPanel';

vi.mock('../../hooks/api', () => ({
  api: {
    chat: {
      today: vi.fn(),
      createConversation: vi.fn(),
      addMessage: vi.fn(),
      resolveProposal: vi.fn(),
    },
    settings: { get: vi.fn() },
    learning: {
      generateFromNote: vi.fn(),
      approve: vi.fn(),
      regenerate: vi.fn(),
      deny: vi.fn(),
    },
  },
}));

/** A /api/chat/stream response whose body stays open until the test closes it,
 * so we can inspect what the app does *while* a reply is generating. */
function openStream() {
  let push!: (chunk: string) => void;
  let pushEvent!: (event: object) => void;
  let close!: (done?: object) => void;
  let fail!: (error: Error) => void;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const encode = new TextEncoder();
      const send = (event: object) =>
        controller.enqueue(encode.encode(`data: ${JSON.stringify(event)}\n\n`));
      push = chunk => send({ content: chunk });
      pushEvent = send;
      close = done => {
        send({ done: true, steps: [], sources: [], proposals: [], ...done });
        controller.enqueue(encode.encode('data: [DONE]\n\n'));
        controller.close();
      };
      // Models a dropped connection (a backgrounded tab, a network blip):
      // the reader's next `read()` rejects, same as a real disconnect.
      fail = error => controller.error(error);
    },
  });
  return {
    response: { ok: true, body } as unknown as Response,
    push,
    pushEvent,
    close,
    fail,
  };
}

const renderChat = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel />
    </QueryClientProvider>
  );
};

/** Send a message and run the reply to completion, staging `proposals`. */
async function sendAndReply(
  message: string,
  stream: ReturnType<typeof openStream>,
  reply: string,
  proposals: object[] = []
) {
  const input = await screen.findByPlaceholderText('Type a message...');
  fireEvent.change(input, { target: { value: message } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() => expect(api.chat.addMessage).toHaveBeenCalled());
  stream.push(reply);
  stream.close({ proposals });
}

beforeEach(() => {
  // Stubbed so the assertion below can prove it is never *called* — the
  // transcript scrolls its own container instead.
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

describe('delegate proposals are durable confirm cards', () => {
  // Cards render purely from the assistant message's persisted metadata now
  // (backend/delegate/runs.py writes it when the run finishes) — no live
  // stream involved, which is the whole point: they survive a reload or a
  // dropped connection. DelegateProposals.test.tsx covers per-kind card
  // content and the accept/dismiss mutation in isolation; these check that
  // ChatPanel actually wires a message's proposals into it.
  const conversationWithProposals = (proposals: object[]) => ({
    id: 'c1',
    messages: [
      {
        id: 'm-user',
        role: 'user',
        content: 'going to the dentist next week',
        metadata: null,
        status: 'done',
        createdAt: '2026-01-01T08:00:00.000Z',
      },
      {
        id: 'm-assistant',
        role: 'assistant',
        content: 'Noted!',
        metadata: JSON.stringify({
          agent: 'delegate',
          steps: [],
          sources: [],
          proposals,
        }),
        status: 'done',
        createdAt: '2026-01-01T08:00:01.000Z',
      },
    ],
  });

  it('renders a pending calendar card straight from metadata', async () => {
    vi.mocked(api.chat.today).mockResolvedValue(
      conversationWithProposals([
        {
          id: 'p1',
          kind: 'calendar',
          status: 'pending',
          data: { title: 'Dentist appointment', date: '2026-08-05', tags: [] },
        },
      ]) as never
    );

    renderChat();

    expect(await screen.findByText('Save as calendar event?')).toBeTruthy();
    expect(screen.getByDisplayValue('Dentist appointment')).toBeTruthy();
  });

  it('accepts a proposal by resolving it on the server, not by saving locally', async () => {
    vi.mocked(api.chat.today).mockResolvedValue(
      conversationWithProposals([
        {
          id: 'p1',
          kind: 'task',
          status: 'pending',
          data: { title: 'call the dentist' },
        },
      ]) as never
    );
    vi.mocked(api.chat.resolveProposal).mockResolvedValue({
      proposal: {
        id: 'p1',
        kind: 'task',
        status: 'accepted',
        data: {},
        result: { id: 'task1' },
      },
    } as never);

    renderChat();
    await screen.findByText('Add to your tasks?');
    fireEvent.click(screen.getByText('Add'));

    await waitFor(() =>
      expect(api.chat.resolveProposal).toHaveBeenCalledWith(
        'm-assistant',
        'p1',
        'accept',
        { title: 'call the dentist' }
      )
    );
  });

  it('dismisses a proposal by resolving it on the server', async () => {
    vi.mocked(api.chat.today).mockResolvedValue(
      conversationWithProposals([
        {
          id: 'p1',
          kind: 'calorie',
          status: 'pending',
          data: { description: 'burger', calories: 650 },
        },
      ]) as never
    );
    vi.mocked(api.chat.resolveProposal).mockResolvedValue({
      proposal: { id: 'p1', kind: 'calorie', status: 'dismissed', data: {} },
    } as never);

    renderChat();
    await screen.findByText('Log calories?');
    fireEvent.click(screen.getByText('Dismiss'));

    await waitFor(() =>
      expect(api.chat.resolveProposal).toHaveBeenCalledWith(
        'm-assistant',
        'p1',
        'dismiss',
        undefined
      )
    );
  });

  it('shows a resolved proposal as a quiet line, not an actionable card', async () => {
    vi.mocked(api.chat.today).mockResolvedValue(
      conversationWithProposals([
        {
          id: 'p1',
          kind: 'flashcards',
          status: 'accepted',
          data: { topic: 'React hooks' },
          result: { count: 4 },
        },
      ]) as never
    );

    renderChat();

    expect(
      await screen.findByText('Queued 4 cards for review in Learning')
    ).toBeTruthy();
    expect(screen.queryByText(/Generate flashcards for/)).toBeNull();
  });

  it("keeps an earlier turn's pending proposal visible once a later turn replies", async () => {
    // Proposals used to live in one piece of "latest turn" React state, so a
    // second reply silently replaced whatever card the first one staged.
    // Reading from each message's own metadata means every still-pending
    // proposal stays up, not just the most recent.
    vi.mocked(api.chat.today).mockResolvedValue({
      id: 'c1',
      messages: [
        {
          id: 'm-user-1',
          role: 'user',
          content: 'remind me to call the dentist',
          metadata: null,
          status: 'done',
          createdAt: '2026-01-01T08:00:00.000Z',
        },
        {
          id: 'm-assistant-1',
          role: 'assistant',
          content: 'Sure.',
          metadata: JSON.stringify({
            agent: 'delegate',
            steps: [],
            sources: [],
            proposals: [
              {
                id: 'p1',
                kind: 'task',
                status: 'pending',
                data: { title: 'call the dentist' },
              },
            ],
          }),
          status: 'done',
          createdAt: '2026-01-01T08:00:01.000Z',
        },
        {
          id: 'm-user-2',
          role: 'user',
          content: 'what time is it',
          metadata: null,
          status: 'done',
          createdAt: '2026-01-01T08:01:00.000Z',
        },
        {
          id: 'm-assistant-2',
          role: 'assistant',
          content: "It's 8am.",
          metadata: JSON.stringify({
            agent: 'delegate',
            steps: [],
            sources: [],
            proposals: [],
          }),
          status: 'done',
          createdAt: '2026-01-01T08:01:01.000Z',
        },
      ],
    } as never);

    renderChat();

    expect(await screen.findByText('Add to your tasks?')).toBeTruthy();
    expect(screen.getByText("It's 8am.")).toBeTruthy();
  });

  it('still shows "Saved to journal" on historical messages saved before the feature was removed', async () => {
    vi.mocked(api.chat.today).mockResolvedValue({
      id: 'c1',
      messages: [
        {
          id: 'm1',
          role: 'user',
          content: 'went for a long run this morning',
          metadata: JSON.stringify({ savedAsJournal: 'entry1' }),
          createdAt: '2026-01-01T08:00:00.000Z',
        },
      ],
    } as never);

    renderChat();

    expect(await screen.findByText('Saved to journal')).toBeTruthy();
  });
});

describe('auto-scroll', () => {
  it('never calls scrollIntoView, which would scroll the whole app', async () => {
    // scrollIntoView walks *every* scrollable ancestor, and `overflow: hidden`
    // does not stop it — it only hides the scrollbar and blocks the user. The
    // shell is `h-dvh` inside a `height: 100%` body, so on mobile (and by a few
    // px elsewhere) the body overflows and scrollIntoView scrolled *the body*,
    // lurching the header, sidebar and SttPanel up with no scrollbar to explain
    // it. Delegation made it obvious by firing the effect on every tool step.
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));

    renderChat();
    await sendAndReply('hello', stream, 'Hi there.');

    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
  });

  /** jsdom has no layout, so every metric the effect reads is 0. Model a real
   * one: a 600px viewport over content whose end sits at `contentBottom` in
   * document space. The sentinel's rect is derived from the live scrollTop, so
   * the effect converges the way it does in a browser instead of stepping the
   * same delta again on every event. */
  const layOut = (
    container: HTMLElement,
    { scrollTop, contentBottom }: { scrollTop: number; contentBottom: number }
  ) => {
    const transcript = container.querySelector(
      '.overflow-y-auto'
    ) as HTMLDivElement;
    Object.defineProperty(transcript, 'clientHeight', {
      value: 600,
      configurable: true,
    });
    Object.defineProperty(transcript, 'scrollHeight', {
      get: () => contentBottom,
      configurable: true,
    });
    transcript.getBoundingClientRect = () =>
      ({ top: 0, bottom: 600 }) as DOMRect;
    // The sentinel is the transcript's last child whenever the break spacer is
    // absent, which it is here (no "New chat" break in this conversation).
    const sentinel = transcript.lastElementChild as HTMLElement;
    const bottom = () => contentBottom - transcript.scrollTop;
    sentinel.getBoundingClientRect = () =>
      ({ top: bottom(), bottom: bottom() }) as DOMRect;
    transcript.scrollTop = scrollTop;
    fireEvent.scroll(transcript);
    return {
      transcript,
      /** Content arriving makes the transcript taller. */
      grow: (by: number) => {
        contentBottom += by;
      },
    };
  };

  const streamADelegateRun = async (stream: ReturnType<typeof openStream>) => {
    const input = await screen.findByPlaceholderText('Type a message...');
    fireEvent.change(input, { target: { value: 'hello' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(api.chat.addMessage).toHaveBeenCalled());
    // A delegate run emits a step and reasoning before a single token of reply.
    stream.pushEvent({ tool: 'web_search', arg: 'x', ok: true, count: 1 });
    stream.pushEvent({ thinking: 'weighing it up' });
    stream.push('Hi there.');
    await screen.findByText(/Hi there\./);
  };

  it('leaves the scroll position alone once the user has scrolled up', async () => {
    // Every step and reasoning delta used to yank the view back down, which is
    // why a streaming answer could not be read back through until it finished.
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));

    const { container } = renderChat();
    await screen.findByPlaceholderText('Type a message...');
    // Scrolled well up, reading back through the conversation.
    const { transcript, grow } = layOut(container, {
      scrollTop: 100,
      contentBottom: 2000,
    });
    grow(300);

    await streamADelegateRun(stream);

    expect(transcript.scrollTop).toBe(100);
    stream.close();
  });

  it('still follows new content while parked at the bottom', async () => {
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));

    const { container } = renderChat();
    await screen.findByPlaceholderText('Type a message...');
    // 2000 - 1400 - 600 = 0 away from the bottom when the user last scrolled.
    const { transcript, grow } = layOut(container, {
      scrollTop: 1400,
      contentBottom: 2000,
    });
    // The reply arriving makes the transcript 300px taller.
    grow(300);

    await streamADelegateRun(stream);

    // Chased the sentinel back to the new bottom, and stopped there rather
    // than stepping the same delta again on every event.
    expect(transcript.scrollTop).toBe(1700);
    stream.close();
  });
});

describe('the "New chat" spacer', () => {
  const withMessages = (messages: object[]) =>
    vi.mocked(api.chat.today).mockResolvedValue({
      id: 'c1',
      messages,
    } as never);

  const breakMsg = {
    id: 'b1',
    role: 'system',
    content: '',
    metadata: JSON.stringify({ break: true }),
    createdAt: '2026-01-01T08:00:00.000Z',
  };
  const userMsg = {
    id: 'm2',
    role: 'user',
    content: 'back again',
    metadata: null,
    createdAt: '2026-01-01T08:01:00.000Z',
  };

  const spacer = (container: HTMLElement) =>
    container.querySelector('[aria-hidden].min-h-\\[60vh\\]');

  it('gives a fresh segment room while the break is still the last thing', async () => {
    withMessages([breakMsg]);
    const { container } = renderChat();
    await screen.findByText('New chat', { selector: 'div' });

    expect(spacer(container)).not.toBeNull();
  });

  it('reclaims the space once the conversation has resumed', async () => {
    // Keyed on "a break exists anywhere", the spacer stayed for the rest of the
    // day — 60vh of empty transcript you could scroll into long after the
    // conversation had filled the screen on its own.
    withMessages([breakMsg, userMsg]);
    const { container } = renderChat();
    await screen.findByText('back again');

    expect(spacer(container)).toBeNull();
  });
});

describe('reply persistence', () => {
  it('recovers the reply from the persisted row when the connection drops mid-stream', async () => {
    // The reply now generates on a background thread independent of this
    // connection (backend/delegate/runs.py) — a drop here must not lose it,
    // because the row it's checkpointing to is what `today` polls next.
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));
    vi.mocked(api.chat.today)
      .mockResolvedValueOnce(null)
      .mockResolvedValue({
        id: 'c1',
        messages: [
          {
            id: 'm-user',
            role: 'user',
            content: 'hello there',
            metadata: null,
            status: 'done',
            createdAt: '2026-01-01T08:00:00.000Z',
          },
          {
            id: 'm-assistant',
            role: 'assistant',
            content: 'General Kenobi',
            metadata: null,
            status: 'done',
            createdAt: '2026-01-01T08:00:01.000Z',
          },
        ],
      } as never);

    renderChat();
    const input = await screen.findByPlaceholderText('Type a message...');
    fireEvent.change(input, { target: { value: 'hello there' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() =>
      expect(api.chat.addMessage).toHaveBeenCalledWith('c1', {
        role: 'user',
        content: 'hello there',
        // Typed, not dictated, and no photo attached — both stay empty.
        rawContent: undefined,
        attachmentIds: [],
      })
    );
    stream.push('General Ken');
    await screen.findByText(/General Ken/);
    stream.fail(new Error('Load failed'));

    // Recovered from the persisted row — no "Error:" wipeout, and the final
    // (server-checkpointed) content, not the partial chunk this tab saw.
    expect(await screen.findByText('General Kenobi')).toBeTruthy();
    expect(screen.queryByText(/^Error:/)).toBeNull();
  });

  it('shows a backend-reported failure via the persisted row rather than a live wipeout', async () => {
    // The bug the delegate replaced: a request that produced no reply, no
    // error and no log entry. The error still has to surface — just from the
    // row the background run wrote it to, not from the dropped connection.
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));
    vi.mocked(api.chat.today)
      .mockResolvedValueOnce(null)
      .mockResolvedValue({
        id: 'c1',
        messages: [
          {
            id: 'm-user',
            role: 'user',
            content: 'log 400 calories',
            metadata: null,
            status: 'done',
            createdAt: '2026-01-01T08:00:00.000Z',
          },
          {
            id: 'm-assistant',
            role: 'assistant',
            content: '',
            metadata: null,
            status: 'error',
            error: 'llama-server is down',
            createdAt: '2026-01-01T08:00:01.000Z',
          },
        ],
      } as never);

    renderChat();
    const input = await screen.findByPlaceholderText('Type a message...');
    fireEvent.change(input, { target: { value: 'log 400 calories' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(api.chat.addMessage).toHaveBeenCalled());

    stream.pushEvent({ error: 'llama-server is down' });
    stream.close();

    expect(await screen.findByText(/llama-server is down/)).toBeTruthy();
    // Never fell back to persisting the reply itself client-side.
    expect(api.chat.addMessage).not.toHaveBeenCalledWith(
      'c1',
      expect.objectContaining({ role: 'assistant' })
    );
  });
});

describe('persisted reasoning', () => {
  const conversationWith = (assistant: object) => ({
    id: 'c1',
    messages: [
      {
        id: 'm-user',
        role: 'user',
        content: 'what should I do about the scheduler?',
        metadata: null,
        status: 'done',
        createdAt: '2026-01-01T08:00:00.000Z',
      },
      {
        id: 'm-assistant',
        role: 'assistant',
        status: 'done',
        createdAt: '2026-01-01T08:00:01.000Z',
        ...assistant,
      },
    ],
  });

  it('renders the reasoning saved on the message, collapsed', async () => {
    // Reasoning used to exist only on the live stream, so a reload showed the
    // answer with no account of how it got there.
    vi.mocked(api.chat.today).mockResolvedValue(
      conversationWith({
        content: 'Leave it as it is.',
        metadata: JSON.stringify({
          agent: 'delegate',
          steps: [],
          sources: [],
          thinking: 'the loop already defers through priority.py',
        }),
      }) as never
    );

    renderChat();

    const summary = await screen.findByText('Reasoning');
    expect(summary.closest('details')?.open).toBe(false);
    expect(
      screen.getByText('the loop already defers through priority.py')
    ).toBeTruthy();
  });

  it('replaces an empty reply bubble with "No reply" and its reasoning', async () => {
    // The bar: a turn that thought and then said nothing used to render as an
    // empty padded rectangle, indistinguishable from a broken message.
    vi.mocked(api.chat.today).mockResolvedValue(
      conversationWith({
        content: '',
        metadata: JSON.stringify({
          agent: 'delegate',
          steps: [],
          sources: [],
          thinking: 'went round in circles',
        }),
      }) as never
    );

    const { container } = renderChat();

    expect(await screen.findByText('No reply')).toBeTruthy();
    expect(screen.getByText('went round in circles')).toBeTruthy();
    // No empty bubble left behind: nothing carries the assistant bubble's
    // surface styling with no text in it.
    const bubbles = Array.from(container.querySelectorAll('.content-text'));
    expect(bubbles.every(b => (b.textContent ?? '').trim().length > 0)).toBe(
      true
    );
  });

  it('names the output limit when that is what ended the reply', async () => {
    // The difference the user can act on: "it had nothing to say" is not a
    // setting, and "it ran out of tokens mid-thought" is.
    vi.mocked(api.chat.today).mockResolvedValue(
      conversationWith({
        content: '',
        metadata: JSON.stringify({
          agent: 'delegate',
          steps: [],
          sources: [],
          thinking: 'still weighing it up',
          truncated: true,
        }),
      }) as never
    );

    renderChat();

    expect(await screen.findByText(/hit the output token limit/)).toBeTruthy();
  });

  it('says nothing extra about a reply that is still generating', async () => {
    // A row that reads empty because the run hasn't produced a token yet is
    // not a blank reply — the thinking label already speaks for it.
    vi.mocked(api.chat.today).mockResolvedValue(
      conversationWith({
        content: '',
        metadata: null,
        status: 'streaming',
      }) as never
    );

    renderChat();

    // ThinkingLabel renders the word twice on purpose (an aria-hidden animated
    // copy and a static one for screen readers), so this matches all of them.
    expect((await screen.findAllByText('Thinking')).length).toBeGreaterThan(0);
    expect(screen.queryByText('No reply')).toBeNull();
  });
});

describe('flashcard draft', () => {
  const noteProposal = [
    { kind: 'flashcard_draft', data: { content: 'warm up before deadlifts' } },
  ];

  it('drafts and previews a lesson card when the delegate stages a flashcard draft', async () => {
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));
    vi.mocked(api.learning.generateFromNote).mockResolvedValue({
      count: 1,
      ids: ['card-1'],
      cards: [
        { id: 'card-1', question: 'Warm up before what?', answer: 'Deadlifts' },
      ],
      folderId: 'folder-1',
    } as never);

    renderChat();
    await sendAndReply(
      'flashcard this: warm up before deadlifts',
      stream,
      'Noted.',
      noteProposal
    );

    await waitFor(() =>
      expect(api.learning.generateFromNote).toHaveBeenCalledWith(
        'warm up before deadlifts'
      )
    );
    expect(await screen.findByText('Warm up before what?')).toBeTruthy();
    expect(screen.getByText('Deadlifts')).toBeTruthy();
    expect(screen.getByText('Save this lesson to Learning?')).toBeTruthy();
  });

  it('removes the preview once the drafted card is approved', async () => {
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));
    vi.mocked(api.learning.generateFromNote).mockResolvedValue({
      count: 1,
      ids: ['card-1'],
      cards: [
        { id: 'card-1', question: 'Warm up before what?', answer: 'Deadlifts' },
      ],
      folderId: 'folder-1',
    } as never);
    vi.mocked(api.learning.approve).mockResolvedValue({
      status: 'approved',
    } as never);

    renderChat();
    await sendAndReply(
      'flashcard this: warm up before deadlifts',
      stream,
      'Noted.',
      noteProposal
    );
    await screen.findByText('Warm up before what?');

    fireEvent.click(screen.getByText('Approve'));

    await waitFor(() =>
      expect(api.learning.approve).toHaveBeenCalledWith('card-1', undefined)
    );
    await waitFor(() =>
      expect(screen.queryByText('Warm up before what?')).toBeNull()
    );
  });

  it('removes the preview once the drafted card is discarded', async () => {
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));
    vi.mocked(api.learning.generateFromNote).mockResolvedValue({
      count: 1,
      ids: ['card-1'],
      cards: [
        { id: 'card-1', question: 'Warm up before what?', answer: 'Deadlifts' },
      ],
      folderId: 'folder-1',
    } as never);
    vi.mocked(api.learning.deny).mockResolvedValue({ success: true } as never);

    renderChat();
    await sendAndReply(
      'flashcard this: warm up before deadlifts',
      stream,
      'Noted.',
      noteProposal
    );
    await screen.findByText('Warm up before what?');

    fireEvent.click(screen.getByText('Discard'));

    await waitFor(() =>
      expect(api.learning.deny).toHaveBeenCalledWith('card-1')
    );
    await waitFor(() =>
      expect(screen.queryByText('Warm up before what?')).toBeNull()
    );
  });

  it('keeps an earlier unactioned draft when a second flashcard draft arrives', async () => {
    const streamA = openStream();
    const streamB = openStream();
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(streamA.response)
        .mockResolvedValueOnce(streamB.response)
    );
    vi.mocked(api.learning.generateFromNote)
      .mockResolvedValueOnce({
        count: 1,
        ids: ['card-a'],
        cards: [
          {
            id: 'card-a',
            question: 'Warm up before what?',
            answer: 'Deadlifts',
          },
        ],
        folderId: 'folder-1',
      } as never)
      .mockResolvedValueOnce({
        count: 1,
        ids: ['card-b'],
        cards: [
          { id: 'card-b', question: 'Stretch when?', answer: 'After lifting' },
        ],
        folderId: 'folder-1',
      } as never);

    renderChat();
    await sendAndReply(
      'flashcard this: warm up before deadlifts',
      streamA,
      'Noted.',
      noteProposal
    );
    await screen.findByText('Warm up before what?');

    await sendAndReply(
      'flashcard this: stretch after too',
      streamB,
      'Got it.',
      [{ kind: 'flashcard_draft', data: { content: 'stretch after too' } }]
    );
    await screen.findByText('Stretch when?');

    // The first draft is still there — a second flashcard draft must not wipe
    // out an earlier one the user hasn't approved or discarded yet.
    expect(screen.getByText('Warm up before what?')).toBeTruthy();
    expect(screen.getByText('Save these lessons to Learning?')).toBeTruthy();
  });
});

// `created_at` is stamped when the run *starts*, which on a local model is
// minutes before there is a reply — so a question and its answer both read
// 7:56 PM and neither label said anything.
describe('message times', () => {
  const exchange = (finishedAt: string | null) => ({
    id: 'c1',
    messages: [
      {
        id: 'm-user',
        role: 'user',
        content: 'how long is a marathon',
        metadata: null,
        status: 'done',
        createdAt: '2026-01-01T19:56:00.000Z',
        finishedAt: null,
      },
      {
        id: 'm-assistant',
        role: 'assistant',
        content: '42.195 km.',
        metadata: null,
        status: 'done',
        createdAt: '2026-01-01T19:56:01.000Z',
        finishedAt,
      },
    ],
  });

  it('times a reply by when it finished, not when it was asked for', async () => {
    vi.mocked(api.chat.today).mockResolvedValue(
      exchange('2026-01-01T19:58:30.000Z')
    );
    const { container } = renderChat();

    await screen.findByText('42.195 km.');
    const times = [...container.querySelectorAll('time')].map(t =>
      t.getAttribute('dateTime')
    );
    expect(times).toEqual([
      '2026-01-01T19:56:00.000Z',
      '2026-01-01T19:58:30.000Z',
    ]);
  });

  it('falls back to the send time on rows written before the column existed', async () => {
    vi.mocked(api.chat.today).mockResolvedValue(exchange(null));
    const { container } = renderChat();

    await screen.findByText('42.195 km.');
    const times = [...container.querySelectorAll('time')].map(t =>
      t.getAttribute('dateTime')
    );
    expect(times).toEqual([
      '2026-01-01T19:56:00.000Z',
      '2026-01-01T19:56:01.000Z',
    ]);
  });
});
