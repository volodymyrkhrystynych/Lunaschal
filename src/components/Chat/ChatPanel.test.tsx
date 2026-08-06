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
      saveCalendar: vi.fn(),
      saveCalories: vi.fn(),
      saveTask: vi.fn(),
    },
    settings: { get: vi.fn() },
    learning: {
      generateForTopic: vi.fn(),
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
    },
  });
  return {
    response: { ok: true, body } as unknown as Response,
    push,
    pushEvent,
    close,
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

describe('delegate proposals become confirm cards', () => {
  it('holds a card back until the reply that mentions it is on screen', async () => {
    const stream = openStream();
    const fetchMock = vi.fn().mockResolvedValue(stream.response);
    vi.stubGlobal('fetch', fetchMock);

    renderChat();
    const input = await screen.findByPlaceholderText('Type a message...');
    fireEvent.change(input, {
      target: { value: 'going to the dentist next week' },
    });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/chat/stream',
        expect.anything()
      )
    );
    stream.push('Noted!');
    await screen.findByText(/Noted!/);

    // Mid-stream: the delegate's work is done but the reply is still coming.
    // A card appearing before the sentence that refers to it reads as a
    // non-sequitur.
    expect(screen.queryByText(/Save as calendar event/)).toBeNull();

    stream.close({
      proposals: [
        {
          kind: 'calendar',
          data: { title: 'Dentist appointment', date: '2026-08-05', tags: [] },
        },
      ],
    });
    expect(await screen.findByText(/Dentist appointment/)).toBeTruthy();
  });

  it('stages a calendar event with no confidence gate', async () => {
    // The old classifier dropped everything under 0.7 with nothing shown,
    // because it was guessing after the fact. A proposal is a commitment.
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));

    renderChat();
    await sendAndReply('going to the dentist next week', stream, 'Nice!', [
      {
        kind: 'calendar',
        data: {
          title: 'Dentist appointment',
          description: 'Checkup',
          date: '2026-08-05',
          tags: [],
        },
      },
    ]);

    expect(await screen.findByText(/Dentist appointment/)).toBeTruthy();
  });

  it('stages a calorie entry and logs it on confirm', async () => {
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));
    vi.mocked(api.chat.saveCalories).mockResolvedValue({ id: 'cal1' });

    renderChat();
    await sendAndReply('I ate a burger, about 650 calories', stream, 'Nice!', [
      { kind: 'calorie', data: { description: 'burger', calories: 650 } },
    ]);

    expect(await screen.findByText(/burger/)).toBeTruthy();
    expect(screen.getByText(/650 cal/)).toBeTruthy();

    fireEvent.click(screen.getByText('Log'));
    await waitFor(() => expect(api.chat.saveCalories).toHaveBeenCalled());
    expect(vi.mocked(api.chat.saveCalories).mock.calls[0][0]).toEqual(
      expect.objectContaining({ description: 'burger', calories: 650 })
    );
  });

  it('stages a task and adds it on confirm', async () => {
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));
    vi.mocked(api.chat.saveTask).mockResolvedValue({ id: 'task1' });

    renderChat();
    await sendAndReply('add call the dentist to my todos', stream, 'Done!', [
      { kind: 'task', data: { title: 'call the dentist', list: 'todo' } },
    ]);

    expect(await screen.findByText('call the dentist')).toBeTruthy();

    fireEvent.click(screen.getByText('Add'));
    await waitFor(() => expect(api.chat.saveTask).toHaveBeenCalled());
    expect(vi.mocked(api.chat.saveTask).mock.calls[0][0]).toEqual(
      expect.objectContaining({ title: 'call the dentist', list: 'todo' })
    );
  });

  it('stages flashcards and queues them on confirm', async () => {
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));
    vi.mocked(api.learning.generateForTopic).mockResolvedValue({
      count: 4,
    } as never);

    renderChat();
    await sendAndReply('quiz me on React hooks', stream, 'Sure.', [
      { kind: 'flashcards', data: { topic: 'React hooks' } },
    ]);

    expect(await screen.findByText(/React hooks/)).toBeTruthy();
    fireEvent.click(screen.getByText('Queue Cards'));
    await waitFor(() =>
      expect(api.learning.generateForTopic).toHaveBeenCalledWith('React hooks')
    );
  });

  it('drops a proposal missing its headline field instead of showing an empty card', async () => {
    // An empty card the user is asked to confirm is a worse failure than a
    // dropped one — and an unknown kind must not throw and take the reply
    // down with it.
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));

    renderChat();
    await sendAndReply('something', stream, 'Sure.', [
      { kind: 'task', data: {} },
      { kind: 'calorie', data: { description: 'burger' } },
      { kind: 'unknown_kind', data: { title: 'x' } },
    ]);

    await waitFor(() =>
      expect(api.chat.addMessage).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({ role: 'assistant' })
      )
    );
    expect(screen.queryByText('Add to your tasks?')).toBeNull();
    expect(screen.queryByText('Log calories?')).toBeNull();
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
  it('keeps the reply on screen with an error, instead of silently vanishing, when saving it fails', async () => {
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));
    vi.mocked(api.chat.addMessage)
      .mockResolvedValueOnce({ id: 'm-user' }) // saving the user's message
      .mockRejectedValueOnce(new Error('network blip')); // saving the reply

    renderChat();
    const input = await screen.findByPlaceholderText('Type a message...');
    fireEvent.change(input, { target: { value: 'hello there' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() =>
      expect(api.chat.addMessage).toHaveBeenCalledWith('c1', {
        role: 'user',
        content: 'hello there',
      })
    );
    stream.push('General Kenobi');
    stream.close();

    // The generated reply must stay visible along with the failure — not
    // wiped out in the same tick it was reported.
    expect(await screen.findByText(/network blip/)).toBeTruthy();
    expect(screen.getByText(/General Kenobi/)).toBeTruthy();
  });

  it('surfaces a mid-stream error rather than showing nothing at all', async () => {
    // The bug the delegate replaced: a request that produced no reply, no
    // error and no log entry.
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));

    renderChat();
    const input = await screen.findByPlaceholderText('Type a message...');
    fireEvent.change(input, { target: { value: 'log 400 calories' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(api.chat.addMessage).toHaveBeenCalled());

    stream.pushEvent({ error: 'llama-server is down' });
    stream.close();

    expect(await screen.findByText(/llama-server is down/)).toBeTruthy();
  });
});

describe('note to self', () => {
  const noteProposal = [
    { kind: 'note', data: { content: 'warm up before deadlifts' } },
  ];

  it('drafts and previews a lesson card when the delegate stages a note', async () => {
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
      'note to self: warm up before deadlifts',
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
      'note to self: warm up before deadlifts',
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
      'note to self: warm up before deadlifts',
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

  it('keeps an earlier unactioned draft when a second note-to-self arrives', async () => {
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
      'note to self: warm up before deadlifts',
      streamA,
      'Noted.',
      noteProposal
    );
    await screen.findByText('Warm up before what?');

    await sendAndReply('note to self: stretch after too', streamB, 'Got it.', [
      { kind: 'note', data: { content: 'stretch after too' } },
    ]);
    await screen.findByText('Stretch when?');

    // The first draft is still there — a second note-to-self must not wipe
    // out an earlier one the user hasn't approved or discarded yet.
    expect(screen.getByText('Warm up before what?')).toBeTruthy();
    expect(screen.getByText('Save these lessons to Learning?')).toBeTruthy();
  });
});
