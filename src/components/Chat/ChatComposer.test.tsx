// @vitest-environment jsdom
/**
 * The chat composer: photos and dictation.
 *
 * The behaviour worth pinning down is that dictation sends on its own. It used
 * to, then stopped: a transcript could mishear a proper noun, and a message is
 * a permanent record, so the box-and-a-second-click gave you somewhere to fix
 * it. Every dictation now goes through two STT models reconciled by an LLM
 * (`merge_transcripts`), and the read-then-click step costs more than it buys —
 * speaking to the chat should be a conversation, not a form.
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
    },
    settings: { get: vi.fn() },
    learning: {
      generateFromNote: vi.fn(),
      approve: vi.fn(),
      regenerate: vi.fn(),
      deny: vi.fn(),
    },
    notes: { due: vi.fn().mockResolvedValue([]) },
  },
}));

// Only the transcript matters here — the microphone plumbing has its own test.
let dictate: (text: string) => void = () => {};
vi.mock('../../hooks/useRecorder', () => ({
  useRecorder: (onTranscript: (text: string) => void) => {
    dictate = onTranscript;
    return {
      status: 'idle',
      canTranscribe: true,
      error: '',
      start: vi.fn(),
      stop: vi.fn(),
    };
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
  it('sends the transcript as a message the moment it arrives', async () => {
    renderChat();
    const input = await ready();
    dictate('had vareniki at Movati');

    await waitFor(() =>
      expect(api.chat.addMessage).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({
          role: 'user',
          content: 'had vareniki at Movati',
        })
      )
    );
    // …and the box is left empty, not holding a copy of what was just sent.
    expect((input as HTMLTextAreaElement).value).toBe('');
  });

  it('sends what was typed and what was spoken as one message', async () => {
    renderChat();
    const input = await ready();
    fireEvent.change(input, { target: { value: 'note:' } });
    dictate('had vareniki at Movati');

    await waitFor(() =>
      expect(api.chat.addMessage).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({ content: 'note: had vareniki at Movati' })
      )
    );
    expect(api.chat.addMessage).toHaveBeenCalledTimes(1);
  });

  it('sends no rawContent — the message already is the transcript', async () => {
    renderChat();
    await ready();
    dictate('had vareniki at Movati');

    await waitFor(() => expect(api.chat.addMessage).toHaveBeenCalled());
    const [, body] = vi.mocked(api.chat.addMessage).mock.calls[0];
    expect(body.rawContent).toBeUndefined();
  });

  it('puts the words back in the box when the send fails', async () => {
    // Nothing was typed, so a dropped transcript is not something the user can
    // retype — it is the only copy of what they said.
    vi.mocked(api.chat.addMessage).mockRejectedValueOnce(new Error('offline'));
    renderChat();
    const input = await ready();
    dictate('had vareniki at Movati');

    await waitFor(() =>
      expect((input as HTMLTextAreaElement).value).toBe(
        'had vareniki at Movati'
      )
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
