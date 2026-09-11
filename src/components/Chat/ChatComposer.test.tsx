// @vitest-environment jsdom
/**
 * The chat composer: photos and dictation.
 *
 * The behaviour worth pinning down is that stopping the recording is the last
 * thing the user has to be present for. The clip is stored on the device and
 * handed to the offline upload queue; the server makes it a message, transcribes
 * it, and answers it. Nothing here waits on `/api/transcribe`, and nothing here
 * calls `addMessage` — a spoken message is created by the upload itself.
 *
 * That replaced a path where the transcript came back to *this browser* first,
 * which meant a screen lock during transcription lost both the recording and the
 * question. The tests below are mostly about what must no longer happen.
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
    chatTodos: { list: vi.fn().mockResolvedValue([]) },
  },
}));

// The microphone plumbing has its own test; what matters here is the hand-off.
// `speak()` stands in for a whole recording: `start` records the target the
// composer asked for, and stopping delivers a stored recording carrying it —
// which is exactly what `useRecorder` does in durable audio mode.
let startArgs: { mode?: string; opts?: Record<string, unknown> } = {};
let deliverRecording: () => Promise<void> = async () => {};
const recorderStart = vi.fn(
  async (mode: string, opts: Record<string, unknown>) => {
    startArgs = { mode, opts };
  }
);
vi.mock('../../hooks/useRecorder', () => ({
  useRecorder: (
    _onTranscript: (text: string) => void,
    _onAudio: unknown,
    options: { onRecording?: (rec: unknown) => Promise<void> | void } = {}
  ) => {
    deliverRecording = async () => {
      await options.onRecording?.({
        id: 'clip-1',
        chat: startArgs.opts?.chat,
      });
    };
    return {
      status: 'idle',
      canTranscribe: true,
      error: '',
      start: recorderStart,
      stop: vi.fn(),
    };
  },
}));

const enqueueChatRecording = vi.fn().mockResolvedValue(undefined);
vi.mock('../../offline/recordingQueue', () => ({
  enqueueChatRecording: (...args: unknown[]) => enqueueChatRecording(...args),
}));

/** Press the mic, speak, press it again. */
async function speak() {
  fireEvent.click(screen.getByTitle('Speak to send'));
  await waitFor(() => expect(recorderStart).toHaveBeenCalled());
  await deliverRecording();
}

const attachment = (over: object = {}) => ({
  id: 'a1',
  conversationId: 'c1',
  messageId: null,
  mime: 'image/jpeg',
  url: '/api/chat/attachments/a1/file',
  kind: 'image',
  description: 'A plate of vareniki. The menu reads "VARENIKI".',
  descriptionStatus: 'done',
  descriptionError: null,
  transcript: null,
  transcriptStatus: null,
  transcriptError: null,
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
  enqueueChatRecording.mockResolvedValue(undefined);
  startArgs = {};
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
  it('hands the clip to the upload queue instead of transcribing it here', async () => {
    renderChat();
    await ready();
    await speak();

    await waitFor(() => expect(enqueueChatRecording).toHaveBeenCalled());
    const [, id, chat] = enqueueChatRecording.mock.calls[0];
    expect(id).toBe('clip-1');
    expect(chat).toMatchObject({ conversationId: 'c1' });
    // The message is created by the upload, not by the browser — and nothing
    // was sent to speech-to-text from here.
    expect(api.chat.addMessage).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalledWith(
      '/api/transcribe',
      expect.anything()
    );
  });

  it('records audio durably, and against a conversation that already exists', async () => {
    // `audio` mode is what tells the server to do the transcribing; `durable`
    // is what keeps the clip when the page dies. The conversation id has to be
    // known before the first chunk, because a clip recovered by the boot sweep
    // knows only what was stored beside it.
    renderChat();
    await ready();
    await speak();

    expect(startArgs.mode).toBe('audio');
    expect(startArgs.opts).toMatchObject({ durable: true });
    expect(startArgs.opts?.chat).toMatchObject({ conversationId: 'c1' });
    expect(
      (startArgs.opts?.chat as { messageId?: string })?.messageId
    ).toBeTruthy();
  });

  it('sends what was typed along with what was spoken, as one message', async () => {
    renderChat();
    const input = await ready();
    fireEvent.change(input, { target: { value: 'note:' } });
    await speak();

    await waitFor(() => expect(enqueueChatRecording).toHaveBeenCalled());
    expect(enqueueChatRecording.mock.calls[0][3]).toMatchObject({
      text: 'note:',
    });
    // …and the box is left empty, not holding a copy of what was just sent.
    expect((input as HTMLTextAreaElement).value).toBe('');
  });

  it('carries staged photos onto the spoken message', async () => {
    renderChat();
    await attachViaPaste();
    await speak();

    await waitFor(() => expect(enqueueChatRecording).toHaveBeenCalled());
    expect(enqueueChatRecording.mock.calls[0][3]).toMatchObject({
      attachmentIds: ['a1'],
    });
  });

  it('keeps the mic usable offline', async () => {
    // Dictation used to need the server before it could become a message at
    // all. The clip is stored on the device now and uploaded when the backend
    // is back, so a flat mic would refuse a recording it can perfectly well
    // keep. The title is the tell: there is no offline wording left.
    renderChat();
    await ready();
    const mic = screen.getByTitle('Speak to send') as HTMLButtonElement;
    expect(mic.disabled).toBe(false);
  });

  it('says the recording is on its way, and never asks for it back', async () => {
    // A paused upload is not an error: the audio is on the device and the queue
    // resumes it. An error banner would suggest something needs doing.
    enqueueChatRecording.mockRejectedValueOnce(new Error('offline'));
    renderChat();
    await ready();
    await speak();

    await waitFor(() => expect(enqueueChatRecording).toHaveBeenCalled());
    expect(screen.queryByText(/offline/i)).toBeNull();
  });

  it('lets go of the mic while an offline upload is still paused', async () => {
    // An offline upload *pauses* rather than failing, so a handler that awaited
    // it would hold the recorder in 'saving' — mic disabled, spinner turning —
    // until the backend came back. The clip is on the device by then, which is
    // the only thing that had to happen before letting go. Being able to record
    // a second message while the first waits is the visible half of that.
    let release!: () => void;
    enqueueChatRecording.mockReturnValueOnce(
      new Promise<void>(resolve => {
        release = resolve;
      })
    );
    renderChat();
    await ready();
    await speak();

    await waitFor(() => expect(enqueueChatRecording).toHaveBeenCalledTimes(1));
    expect(screen.getByText(/sending your recording/i)).toBeTruthy();
    const mic = screen.getByTitle('Speak to send') as HTMLButtonElement;
    expect(mic.disabled).toBe(false);

    // A second message can be spoken while the first is still queued.
    await speak();
    await waitFor(() => expect(enqueueChatRecording).toHaveBeenCalledTimes(2));
    release();
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
