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
      classify: vi.fn(),
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
      <ChatPanel mode="chat" />
    </QueryClientProvider>
  );
};

beforeEach(() => {
  // jsdom has no layout, so the auto-scroll-to-bottom effect needs a stub.
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

describe('Chat send ordering', () => {
  it('does not classify until the reply has finished streaming', async () => {
    // Still true on llama-server: the chat model runs with two slots, so a
    // classify fired in parallel with the reply takes the other one and competes
    // for the same CPU-resident experts — the user waits out a second generation
    // before their first token.
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

  it('still offers to save when the classifier comes back calendar', async () => {
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));
    vi.mocked(api.chat.classify).mockResolvedValue({
      intent: 'calendar',
      confidence: 0.9,
      calendarEvent: {
        title: 'Dentist appointment',
        description: 'Checkup',
        date: '2026-08-05',
        tags: [],
      },
    } as never);

    renderChat();
    const input = await screen.findByPlaceholderText('Type a message...');
    fireEvent.change(input, {
      target: { value: 'going to the dentist next week' },
    });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(api.chat.addMessage).toHaveBeenCalled());
    stream.push('Nice!');
    stream.close();

    expect(await screen.findByText(/Dentist appointment/)).toBeTruthy();
  });

  it('offers to log calories when the classifier comes back calorie_log', async () => {
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));
    vi.mocked(api.chat.classify).mockResolvedValue({
      intent: 'calorie_log',
      confidence: 0.9,
      calorieLog: { description: 'burger', calories: 650 },
    } as never);
    vi.mocked(api.chat.saveCalories).mockResolvedValue({ id: 'cal1' });

    renderChat();
    const input = await screen.findByPlaceholderText('Type a message...');
    fireEvent.change(input, {
      target: { value: 'I ate a burger, about 650 calories' },
    });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(api.chat.addMessage).toHaveBeenCalled());
    stream.push('Nice!');
    stream.close();

    expect(await screen.findByText(/burger/)).toBeTruthy();
    expect(screen.getByText(/650 cal/)).toBeTruthy();

    fireEvent.click(screen.getByText('Log'));
    await waitFor(() => expect(api.chat.saveCalories).toHaveBeenCalled());
    expect(vi.mocked(api.chat.saveCalories).mock.calls[0][0]).toEqual(
      expect.objectContaining({ description: 'burger', calories: 650 })
    );
  });

  it('offers to add a task when the classifier comes back create_task', async () => {
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));
    vi.mocked(api.chat.classify).mockResolvedValue({
      intent: 'create_task',
      confidence: 0.9,
      createTask: { title: 'call the dentist' },
    } as never);
    vi.mocked(api.chat.saveTask).mockResolvedValue({ id: 'task1' });

    renderChat();
    const input = await screen.findByPlaceholderText('Type a message...');
    fireEvent.change(input, {
      target: { value: 'add call the dentist to my todos' },
    });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(api.chat.addMessage).toHaveBeenCalled());
    stream.push('Done!');
    stream.close();

    expect(await screen.findByText('call the dentist')).toBeTruthy();

    fireEvent.click(screen.getByText('Add'));
    await waitFor(() => expect(api.chat.saveTask).toHaveBeenCalled());
    expect(vi.mocked(api.chat.saveTask).mock.calls[0][0]).toEqual(
      expect.objectContaining({ title: 'call the dentist' })
    );
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
});

describe('note to self', () => {
  const sendAndClassify = async (
    message: string,
    stream: ReturnType<typeof openStream>,
    reply: string
  ) => {
    const input = await screen.findByPlaceholderText('Type a message...');
    fireEvent.change(input, { target: { value: message } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(api.chat.addMessage).toHaveBeenCalled());
    stream.push(reply);
    stream.close();
  };

  it('drafts and previews a lesson card when the classifier returns note_to_self', async () => {
    const stream = openStream();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream.response));
    vi.mocked(api.chat.classify).mockResolvedValue({
      intent: 'note_to_self',
      confidence: 0.9,
      noteToSelf: { content: 'warm up before deadlifts' },
    } as never);
    vi.mocked(api.learning.generateFromNote).mockResolvedValue({
      count: 1,
      ids: ['card-1'],
      cards: [
        { id: 'card-1', question: 'Warm up before what?', answer: 'Deadlifts' },
      ],
      folderId: 'folder-1',
    } as never);

    renderChat();
    await sendAndClassify(
      'note to self: warm up before deadlifts',
      stream,
      'Noted.'
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
    vi.mocked(api.chat.classify).mockResolvedValue({
      intent: 'note_to_self',
      confidence: 0.9,
      noteToSelf: { content: 'warm up before deadlifts' },
    } as never);
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
    await sendAndClassify(
      'note to self: warm up before deadlifts',
      stream,
      'Noted.'
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
    vi.mocked(api.chat.classify).mockResolvedValue({
      intent: 'note_to_self',
      confidence: 0.9,
      noteToSelf: { content: 'warm up before deadlifts' },
    } as never);
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
    await sendAndClassify(
      'note to self: warm up before deadlifts',
      stream,
      'Noted.'
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
    vi.mocked(api.chat.classify)
      .mockResolvedValueOnce({
        intent: 'note_to_self',
        confidence: 0.9,
        noteToSelf: { content: 'warm up before deadlifts' },
      } as never)
      .mockResolvedValueOnce({
        intent: 'note_to_self',
        confidence: 0.9,
        noteToSelf: { content: 'stretch after too' },
      } as never);
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
    await sendAndClassify(
      'note to self: warm up before deadlifts',
      streamA,
      'Noted.'
    );
    await screen.findByText('Warm up before what?');

    await sendAndClassify(
      'note to self: stretch after too',
      streamB,
      'Got it.'
    );
    await screen.findByText('Stretch when?');

    // The first draft is still there — a second note-to-self must not wipe
    // out an earlier one the user hasn't approved or discarded yet.
    expect(screen.getByText('Warm up before what?')).toBeTruthy();
    expect(screen.getByText('Save these lessons to Learning?')).toBeTruthy();
  });
});
