// @vitest-environment jsdom
/**
 * The chat composer: photos, dictation, and the correction pass between them.
 *
 * The behaviour worth pinning down is that dictation no longer sends. It used to
 * POST the transcript straight through as the message — the only view in the app
 * that did — which meant a misheard name became a permanent record with no
 * moment in which to fix it, and left nowhere for a correction pass to run.
 */
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
      uploadAttachments: vi.fn(),
      getAttachment: vi.fn(),
      deleteAttachment: vi.fn(),
      polishTranscript: vi.fn(),
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

// Only the transcript matters here — the microphone plumbing has its own test.
let dictate: (text: string) => void = () => {};
vi.mock('../../hooks/useRecorder', () => ({
  useRecorder: (onTranscript: (text: string) => void) => {
    dictate = onTranscript;
    return { status: 'idle', error: '', start: vi.fn(), stop: vi.fn() };
  },
}));

const attachment = (over: object = {}) => ({
  id: 'a1',
  conversationId: 'c1',
  messageId: null,
  mime: 'image/jpeg',
  url: '/api/chat/attachments/a1/file',
  description: 'A plate of vareniki. The menu reads "VARENIKI".',
  descriptionStatus: 'done',
  descriptionError: null,
  position: 0,
  createdAt: '2026-01-01T08:00:00.000Z',
  ...over,
});

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

const emptyStream = () =>
  ({
    ok: true,
    body: new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: [DONE]\n\n'));
        controller.close();
      },
    }),
  }) as unknown as Response;

beforeEach(() => {
  // The api mock is module-level, so call history survives `restoreAllMocks`.
  // Several assertions below are "was this never called", which needs it clear.
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(emptyStream()));
  vi.mocked(api.chat.today).mockResolvedValue(null);
  vi.mocked(api.settings.get).mockResolvedValue({
    llamaUrl: 'http://localhost:8080',
    llamaVisionModel: 'gemma4-12b-omni',
  } as never);
  vi.mocked(api.chat.createConversation).mockResolvedValue({ id: 'c1' });
  vi.mocked(api.chat.addMessage).mockResolvedValue({ id: 'm1' });
  vi.mocked(api.chat.uploadAttachments).mockResolvedValue([
    attachment() as never,
  ]);
  vi.mocked(api.chat.deleteAttachment).mockResolvedValue({ success: true });
  vi.mocked(api.chat.polishTranscript).mockResolvedValue({
    raw: 'had vary nikki at motivate',
    corrected: 'had vareniki at Movati',
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

/** The composer's textarea, once settings have loaded — until they do the
 * placeholder still reads "Configure AI provider first...". */
async function ready() {
  return screen.findByPlaceholderText('Type a message...');
}

const attachViaPaste = async () => {
  const composer = (await ready()).closest('div.border-t') as HTMLElement;
  fireEvent.paste(composer, {
    clipboardData: {
      files: [new File(['x'], 'meal.jpg', { type: 'image/jpeg' })],
    },
  });
  await waitFor(() => expect(api.chat.uploadAttachments).toHaveBeenCalled());
};

describe('dictation', () => {
  it('puts the transcript in the box instead of sending it', async () => {
    renderChat();
    const input = await ready();
    dictate('had vary nikki at motivate');

    await waitFor(() =>
      expect((input as HTMLTextAreaElement).value).toBe(
        'had vareniki at Movati'
      )
    );
    expect(api.chat.addMessage).not.toHaveBeenCalled();
  });

  it('keeps the verbatim transcript visible before sending', async () => {
    renderChat();
    await ready();
    dictate('had vary nikki at motivate');
    // The whole reason not to auto-send: the user can see what was heard and
    // fix it, rather than finding out afterwards.
    expect(await screen.findByText('had vary nikki at motivate')).toBeTruthy();
  });

  it('sends the verbatim transcript as rawContent alongside the corrected text', async () => {
    renderChat();
    const input = await ready();
    dictate('had vary nikki at motivate');
    await waitFor(() =>
      expect((input as HTMLTextAreaElement).value).toBe(
        'had vareniki at Movati'
      )
    );
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() =>
      expect(api.chat.addMessage).toHaveBeenCalledWith('c1', {
        role: 'user',
        content: 'had vareniki at Movati',
        metadata: undefined,
        rawContent: 'had vary nikki at motivate',
        attachmentIds: [],
      })
    );
  });

  it('leaves rawContent unset when the message was typed', async () => {
    renderChat();
    const input = await ready();
    fireEvent.change(input, { target: { value: 'typed this one' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() =>
      expect(api.chat.addMessage).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({ rawContent: undefined })
      )
    );
  });

  it('keeps the raw transcript when the correction pass fails', async () => {
    vi.mocked(api.chat.polishTranscript).mockRejectedValue(
      new Error('llama-server is down')
    );
    renderChat();
    const input = await ready();
    dictate('had vary nikki at motivate');

    await waitFor(() =>
      expect((input as HTMLTextAreaElement).value).toBe(
        'had vary nikki at motivate'
      )
    );
  });

  it('only rewrites the dictated part, leaving typed text alone', async () => {
    renderChat();
    const input = await ready();
    fireEvent.change(input, { target: { value: 'note:' } });
    dictate('had vary nikki at motivate');

    await waitFor(() =>
      expect((input as HTMLTextAreaElement).value).toBe(
        'note: had vareniki at Movati'
      )
    );
  });
});

describe('photos', () => {
  it('uploads a pasted photo and shows it staged', async () => {
    renderChat();
    await attachViaPaste();
    expect((await screen.findAllByAltText(/vareniki/i)).length).toBeGreaterThan(
      0
    );
  });

  it('ignores a paste that carries no image', async () => {
    renderChat();
    const composer = (await ready()).closest('div.border-t') as HTMLElement;
    fireEvent.paste(composer, { clipboardData: { files: [] } });
    expect(api.chat.uploadAttachments).not.toHaveBeenCalled();
  });

  it('says so when a paste carried something it cannot attach', async () => {
    renderChat();
    const composer = (await ready()).closest('div.border-t') as HTMLElement;
    fireEvent.paste(composer, {
      clipboardData: {
        files: [new File(['x'], 'notes.pdf', { type: 'application/pdf' })],
      },
    });
    expect(await screen.findByText(/photos only/i)).toBeTruthy();
  });

  it('sends the staged photo ids with the message', async () => {
    renderChat();
    await attachViaPaste();
    const input = await ready();
    fireEvent.change(input, { target: { value: 'what is this' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() =>
      expect(api.chat.addMessage).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({ attachmentIds: ['a1'] })
      )
    );
  });

  it('allows sending a photo with no text at all', async () => {
    renderChat();
    await attachViaPaste();
    // A photo on its own is a complete message — "what is this?" is implied.
    const send = screen.getByRole('button', { name: 'Send' });
    expect((send as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(send);
    await waitFor(() =>
      expect(api.chat.addMessage).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({ content: '', attachmentIds: ['a1'] })
      )
    );
  });

  it('removing a staged photo deletes it and drops it from the send', async () => {
    renderChat();
    await attachViaPaste();
    fireEvent.click(screen.getByLabelText('Remove photo'));

    await waitFor(() =>
      expect(api.chat.deleteAttachment).toHaveBeenCalledWith('a1')
    );
    const send = screen.getByRole('button', { name: 'Send' });
    expect((send as HTMLButtonElement).disabled).toBe(true);
  });

  it('says the photo is still being read', async () => {
    vi.mocked(api.chat.uploadAttachments).mockResolvedValue([
      attachment({ descriptionStatus: 'running', description: null }) as never,
    ]);
    renderChat();
    await attachViaPaste();
    expect(await screen.findByText(/Reading the photo/i)).toBeTruthy();
  });

  it('warns when there is no model configured to read photos', async () => {
    // A spinner that never resolves is the failure mode; saying so is the fix.
    vi.mocked(api.settings.get).mockResolvedValue({
      llamaUrl: 'http://localhost:8080',
      llamaVisionModel: '',
    } as never);
    renderChat();
    await attachViaPaste();
    expect(
      await screen.findByText(/nothing is set up to read them/i)
    ).toBeTruthy();
  });

  it('shows no reading status when the chat model reads photos itself', async () => {
    // That path has no pre-read phase at all — the picture rides into the turn.
    vi.mocked(api.settings.get).mockResolvedValue({
      llamaUrl: 'http://localhost:8080',
      llamaVisionModel: '',
      llamaChatVision: true,
    } as never);
    vi.mocked(api.chat.uploadAttachments).mockResolvedValue([
      attachment({ descriptionStatus: null, description: null }) as never,
    ]);
    renderChat();
    await attachViaPaste();

    expect(screen.queryByText(/Reading the photo/i)).toBeNull();
    expect(screen.queryByText(/nothing is set up to read them/i)).toBeNull();
  });

  it('feeds the staged photo ids to the correction pass', async () => {
    renderChat();
    await attachViaPaste();
    dictate('had vary nikki');
    await waitFor(() =>
      expect(api.chat.polishTranscript).toHaveBeenCalledWith('had vary nikki', [
        'a1',
      ])
    );
  });
});

describe('a sent message', () => {
  it('renders its photos and what was dictated', async () => {
    vi.mocked(api.chat.today).mockResolvedValue({
      id: 'c1',
      messages: [
        {
          id: 'm-user',
          role: 'user',
          content: 'had vareniki at Movati',
          rawContent: 'had vary nikki at motivate',
          metadata: null,
          status: 'done',
          createdAt: '2026-01-01T08:00:00.000Z',
          attachments: [attachment({ messageId: 'm-user' })],
        },
      ],
    } as never);
    renderChat();

    expect(await screen.findByText('had vareniki at Movati')).toBeTruthy();
    expect(screen.getByText('As dictated')).toBeTruthy();
    expect(screen.getByText('had vary nikki at motivate')).toBeTruthy();
    // The reading is the alt text: it is the only thing the model ever saw.
    expect(screen.getByAltText(/vareniki/i)).toBeTruthy();
  });

  it('forwards a past message’s photo ids so the model keeps seeing it', async () => {
    vi.mocked(api.chat.today).mockResolvedValue({
      id: 'c1',
      messages: [
        {
          id: 'm-user',
          role: 'user',
          content: 'had vareniki',
          metadata: null,
          status: 'done',
          createdAt: '2026-01-01T08:00:00.000Z',
          attachments: [attachment({ messageId: 'm-user' })],
        },
      ],
    } as never);
    renderChat();
    const input = await ready();
    fireEvent.change(input, { target: { value: 'how many calories?' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    const body = JSON.parse(
      (vi.mocked(fetch).mock.calls[0][1] as RequestInit).body as string
    );
    expect(body.messages[0].attachmentIds).toEqual(['a1']);
  });
});
