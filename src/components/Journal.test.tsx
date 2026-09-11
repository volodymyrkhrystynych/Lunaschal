// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import {
  QueryClient,
  QueryClientProvider,
  onlineManager,
} from '@tanstack/react-query';
import { Journal } from './Journal';
import { ShortcutProvider } from '../shortcuts/ShortcutProvider';
import { api, type JournalEntry } from '../hooks/api';
import { enqueueRecordingUpload } from '../offline/recordingQueue';
import { deleteRecording } from '../offline/recordingStore';
import { storePhoto } from '../offline/photoStore';
import { enqueueJournalAttachment } from '../offline/photoQueue';

const { ENTRIES } = vi.hoisted(() => {
  const ENTRIES: JournalEntry[] = [
    {
      id: 'e1',
      content: 'First entry',
      rawContent: null,
      title: null,
      tags: null,
      curatedTags: [],
      ficRefs: [],
      createdAt: '2026-07-02T10:00:00Z',
      updatedAt: '',
    },
    {
      id: 'e2',
      content: 'Second entry',
      rawContent: null,
      title: null,
      tags: null,
      curatedTags: [],
      ficRefs: [],
      createdAt: '2026-07-01T10:00:00Z',
      updatedAt: '',
    },
  ];
  return { ENTRIES };
});

vi.mock('../hooks/api', () => ({
  api: {
    journal: {
      list: vi.fn().mockResolvedValue(ENTRIES),
      search: vi.fn().mockResolvedValue([]),
      create: vi.fn(),
      update: vi.fn(),
      delete: vi.fn(),
      polish: vi.fn(),
      mergeCandidates: vi.fn().mockResolvedValue([]),
      merge: vi.fn(),
      attachments: {
        list: vi.fn(),
        upload: vi.fn(),
        rename: vi.fn(),
        delete: vi.fn(),
        transcribe: vi.fn(),
        link: vi.fn().mockResolvedValue({ id: 'v1' }),
      },
      voiceDrafts: {
        list: vi.fn().mockResolvedValue([]),
        retry: vi.fn(),
        delete: vi.fn(),
      },
    },
    curatedTags: { list: vi.fn().mockResolvedValue([]) },
    newspapers: { journalIssues: vi.fn().mockResolvedValue([]) },
    study: { journal: vi.fn().mockResolvedValue([]) },
    transcriptions: { list: vi.fn().mockResolvedValue([]), delete: vi.fn() },
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
  },
}));

// The Record buttons. The microphone plumbing is covered by useRecorder's own
// test; what matters here is that pressing one *stages* a clip — and only
// stages it. `start` delivers the stored recording synchronously, so a test can
// drive the whole thing by clicking the real button and never has to reach for
// a particular hook instance (there are two live at once once the composer is
// open).
const STORED_RECORDING = {
  id: 'rec-1',
  mimeType: 'audio/webm',
  startedAt: 1_000,
  endedAt: 43_000,
};
/** The options each `start()` was called with, newest last. */
const recorderStarts: Array<Record<string, unknown>> = [];
let nextRecordingId = 0;

vi.mock('../hooks/useRecorder', () => ({
  useRecorder: (
    _onTranscript: unknown,
    _onAudio: unknown,
    options: {
      onRecording?: (rec: typeof STORED_RECORDING) => void | Promise<void>;
    } = {}
  ) => ({
    status: 'idle',
    canTranscribe: true,
    error: '',
    start: vi.fn(async (_mode: string, opts: Record<string, unknown> = {}) => {
      recorderStarts.push(opts);
      const id = nextRecordingId ? `rec-${nextRecordingId + 1}` : 'rec-1';
      nextRecordingId += 1;
      await options.onRecording?.({ ...STORED_RECORDING, id });
    }),
    stop: vi.fn(),
  }),
}));

vi.mock('../offline/recordingQueue', () => ({
  enqueueRecordingUpload: vi.fn().mockResolvedValue(undefined),
  enqueueFoodRecording: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../offline/recordingStore', () => ({
  deleteRecording: vi.fn().mockResolvedValue(undefined),
}));

// The durable path a staged file now takes: on the device first, then into the
// offline queue. Both are asserted rather than the raw upload, because the raw
// upload is exactly what a bad connection used to lose.
vi.mock('../offline/photoStore', () => ({
  storePhoto: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../offline/photoQueue', () => ({
  enqueueJournalAttachment: vi.fn().mockResolvedValue(undefined),
}));

class FakeEventSource {
  onmessage: unknown = null;
  close() {}
}

function renderJournal(props: Parameters<typeof Journal>[0] = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="journal" onViewChange={() => {}}>
        <Journal {...props} />
      </ShortcutProvider>
    </QueryClientProvider>
  );
}

// D once descends from the sidebar level into the entry list, D again drills
// into the selected entry.
const openEditWithKeyboard = () => {
  fireEvent.keyDown(window, { code: 'KeyD' });
  fireEvent.keyDown(window, { code: 'KeyD' });
};

describe('Journal keyboard editing', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', FakeEventSource);
    Element.prototype.scrollIntoView = vi.fn();
  });

  it('restores an unfinished correction after the whole view is recreated', async () => {
    const first = renderJournal();
    await screen.findByText('First entry');
    openEditWithKeyboard();
    fireEvent.change(screen.getByDisplayValue('First entry'), {
      target: { value: 'Corrected transcript, still unsaved' },
    });
    first.unmount();
    renderJournal();
    const restored = await screen.findByDisplayValue(
      'Corrected transcript, still unsaved'
    );
    expect(restored.tagName).toBe('TEXTAREA');
  });

  it('D opens the selected entry for editing with the textarea focused', async () => {
    renderJournal();
    await screen.findByText('First entry');

    openEditWithKeyboard();

    const textarea = screen.getByDisplayValue('First entry');
    expect(textarea.tagName).toBe('TEXTAREA');
    expect(document.activeElement).toBe(textarea);
  });

  it('Escape closes the editor', async () => {
    renderJournal();
    await screen.findByText('First entry');

    openEditWithKeyboard();
    fireEvent.keyDown(screen.getByDisplayValue('First entry'), {
      key: 'Escape',
    });

    expect(screen.queryByDisplayValue('First entry')).toBeNull();
    expect(screen.getByText('First entry')).toBeTruthy(); // back to the read view
  });

  it('A closes the editor when it is open but not focused', async () => {
    renderJournal();
    await screen.findByText('First entry');

    openEditWithKeyboard();
    (document.activeElement as HTMLElement).blur();
    fireEvent.keyDown(window, { code: 'KeyA' });

    expect(screen.queryByDisplayValue('First entry')).toBeNull();
    expect(screen.getByText('First entry')).toBeTruthy();
  });

  it('A with no editor open just backs out without touching the entries', async () => {
    renderJournal();
    await screen.findByText('First entry');

    fireEvent.keyDown(window, { code: 'KeyD' });
    fireEvent.keyDown(window, { code: 'KeyA' });

    expect(screen.getByText('First entry')).toBeTruthy();
    expect(screen.queryByDisplayValue('First entry')).toBeNull();
  });
});

describe('Journal edit-mode recording', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    recorderStarts.length = 0;
    nextRecordingId = 0;
    vi.mocked(api.journal.list).mockResolvedValue(ENTRIES);
    vi.stubGlobal('EventSource', FakeEventSource);
    Element.prototype.scrollIntoView = vi.fn();
  });

  const pressRecord = () =>
    fireEvent.click(screen.getByLabelText('Record into this entry'));

  it('stages the clip instead of writing into the draft', async () => {
    // The whole point of the change. Recording used to fetch a transcript in
    // the browser and paste it into the textarea, which meant waiting out a CPU
    // transcription before a second thought could be recorded.
    renderJournal();
    await screen.findByText('First entry');
    openEditWithKeyboard();

    const textarea = screen.getByDisplayValue(
      'First entry'
    ) as HTMLTextAreaElement;
    pressRecord();

    expect(await screen.findByText('Clip 1 · 0:42')).toBeTruthy();
    expect(textarea.value).toBe('First entry');
    // Staging is not saving — nothing has been uploaded, and the entry has not
    // changed.
    expect(enqueueRecordingUpload).not.toHaveBeenCalled();
    expect(api.journal.update).not.toHaveBeenCalled();
  });

  it('records against the entry that is open, from the first chunk', async () => {
    renderJournal();
    await screen.findByText('First entry');
    openEditWithKeyboard();

    pressRecord();

    await waitFor(() => expect(recorderStarts).toHaveLength(1));
    // Written beside the audio, so a clip rescued on a later boot lands back on
    // this entry rather than arriving as one of its own.
    expect(recorderStarts[0]).toMatchObject({ durable: true, entryId: 'e1' });
  });

  it('uploads the clips when the editor closes, Cancel included', async () => {
    // The entry already exists, so there is nothing for a clip to be "not
    // saved" into — discarding a recording because the text edit beside it was
    // abandoned is the silent audio loss the durable path exists to refuse.
    renderJournal();
    await screen.findByText('First entry');
    openEditWithKeyboard();
    pressRecord();
    await screen.findByText('Clip 1 · 0:42');

    fireEvent.click(screen.getByText('Cancel'));

    await waitFor(() =>
      expect(enqueueRecordingUpload).toHaveBeenCalledWith(
        expect.anything(),
        'rec-1',
        'Recording',
        { entryId: 'e1' }
      )
    );
    expect(api.journal.update).not.toHaveBeenCalled();
  });

  it('reads Record, not Transcribe', async () => {
    renderJournal();
    await screen.findByText('First entry');
    openEditWithKeyboard();

    expect(screen.getByText('● Record')).toBeTruthy();
    expect(screen.queryByText('● Transcribe')).toBeNull();
  });
});

describe('Journal new-entry keyboard save', () => {
  const createMock = api.journal.create as ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.stubGlobal('EventSource', FakeEventSource);
    Element.prototype.scrollIntoView = vi.fn();
    createMock.mockReset();
    createMock.mockResolvedValue({ id: 'new' });
  });

  async function openNewEntry() {
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));
    return screen.getByPlaceholderText(
      'Write your journal entry...'
    ) as HTMLTextAreaElement;
  }

  it('restores a new entry draft and discards it on explicit Cancel', async () => {
    const first = renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));
    fireEvent.change(
      screen.getByPlaceholderText('Write your journal entry...'),
      {
        target: { value: 'New entry correction' },
      }
    );
    first.unmount();
    const second = renderJournal();
    await screen.findByDisplayValue('New entry correction');
    fireEvent.click(screen.getByText('Cancel'));
    second.unmount();
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));
    expect(
      (
        screen.getByPlaceholderText(
          'Write your journal entry...'
        ) as HTMLTextAreaElement
      ).value
    ).toBe('');
  });

  it('saves the entry when Enter is pressed', async () => {
    const textarea = await openNewEntry();
    fireEvent.change(textarea, { target: { value: 'a thought' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    await waitFor(() =>
      // A client-generated ULID is included so offline creates replay
      // idempotently.
      expect(createMock).toHaveBeenCalledWith(
        expect.objectContaining({
          content: 'a thought',
          id: expect.any(String),
        })
      )
    );
  });

  it('does not save on Shift+Enter (newline instead)', async () => {
    const textarea = await openNewEntry();
    fireEvent.change(textarea, { target: { value: 'a thought' } });
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true });

    expect(createMock).not.toHaveBeenCalled();
  });

  it('does not save a whitespace-only entry on Enter', async () => {
    const textarea = await openNewEntry();
    fireEvent.change(textarea, { target: { value: '   ' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    expect(createMock).not.toHaveBeenCalled();
  });

  it('closes the compose box on submit even while offline (mutation paused)', async () => {
    // Offline the create mutation is paused, so onSuccess never fires; the form
    // must still reset on submit or it lingers open showing a duplicate of the
    // optimistically-inserted entry.
    onlineManager.setOnline(false);
    try {
      const textarea = await openNewEntry();
      fireEvent.change(textarea, { target: { value: 'offline thought' } });
      fireEvent.keyDown(textarea, { key: 'Enter' });

      await waitFor(() =>
        expect(
          screen.queryByPlaceholderText('Write your journal entry...')
        ).toBeNull()
      );
      expect(createMock).not.toHaveBeenCalled(); // paused, not sent
    } finally {
      onlineManager.setOnline(true);
    }
  });
});

// With llama-server down, polish used to fail silently: the request came back
// 200 with the raw transcript written over the entry, and the button simply
// stopped saying "Polishing...". The failure now has to reach the screen.
describe('Journal polish failures', () => {
  const listMock = api.journal.list as ReturnType<typeof vi.fn>;
  const polishMock = api.journal.polish as ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.stubGlobal('EventSource', FakeEventSource);
    Element.prototype.scrollIntoView = vi.fn();
    polishMock.mockReset();
    listMock.mockResolvedValue([
      {
        id: 'e-raw',
        content: 'Polished prose.',
        rawContent: 'so today was rough i barely slept',
        title: null,
        tags: null,
        curatedTags: [],
        ficRefs: [],
        attachments: [],
        createdAt: '2026-07-02T10:00:00Z',
        updatedAt: '',
      },
    ]);
  });

  afterEach(() => listMock.mockResolvedValue(ENTRIES));

  it('shows why the polish failed and leaves the entry on screen', async () => {
    polishMock.mockRejectedValue(
      new Error('Polish unavailable: Connection error.')
    );
    renderJournal();

    fireEvent.click(await screen.findByText('Polish'));

    expect(
      await screen.findByText(/Polish unavailable: Connection error\./)
    ).toBeTruthy();
    // The button goes back to being clickable rather than staying stuck.
    expect(await screen.findByText('Polish')).toBeTruthy();
    expect(screen.getByText('Polished prose.')).toBeTruthy();
  });

  it('shows no error after a successful polish', async () => {
    polishMock.mockResolvedValue({
      success: true,
      content: 'So today was rough.',
    });
    renderJournal();

    fireEvent.click(await screen.findByText('Polish'));

    await waitFor(() => expect(polishMock).toHaveBeenCalledWith('e-raw'));
    expect(screen.queryByText(/left unchanged/)).toBeNull();
  });
});

// Pasting into the compose box happens before the entry exists server-side, so
// the files are held and uploaded once the create lands.
describe('Journal new-entry attachments', () => {
  const createMock = api.journal.create as ReturnType<typeof vi.fn>;
  const uploadMock = api.journal.attachments.upload as ReturnType<typeof vi.fn>;

  beforeEach(() => {
    recorderStarts.length = 0;
    nextRecordingId = 0;
    vi.mocked(deleteRecording).mockClear();
    vi.mocked(enqueueRecordingUpload).mockClear();
    vi.mocked(storePhoto).mockClear();
    vi.mocked(storePhoto).mockResolvedValue(
      undefined as unknown as Awaited<ReturnType<typeof storePhoto>>
    );
    vi.mocked(enqueueJournalAttachment).mockClear();
    vi.stubGlobal('EventSource', FakeEventSource);
    Element.prototype.scrollIntoView = vi.fn();
    createMock.mockReset();
    createMock.mockResolvedValue({ id: 'new' });
    uploadMock.mockReset();
    uploadMock.mockResolvedValue({});
  });

  async function composeWith(files: File[]) {
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));
    const textarea = screen.getByPlaceholderText('Write your journal entry...');
    fireEvent.paste(textarea, {
      clipboardData: { files: files as unknown as FileList },
    });
    return textarea;
  }

  it('offers a record button in the compose box too', async () => {
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));

    expect(screen.getByTestId('journal-new-entry-transcribe')).toBeTruthy();
    expect(screen.getByText('● Record')).toBeTruthy();
  });

  it('stages the clip and leaves the draft alone', async () => {
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));
    const textarea = screen.getByPlaceholderText(
      'Write your journal entry...'
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'a thought' } });

    fireEvent.click(screen.getByTestId('journal-new-entry-transcribe'));

    // A chip, not text in the box: the transcript is the server's job now, and
    // waiting for it here is what made the composer feel stuck.
    expect(await screen.findByText('Clip 1 · 0:42')).toBeTruthy();
    expect(textarea.value).toBe('a thought');
    expect(uploadMock).not.toHaveBeenCalled();
  });

  it('takes several clips, and sends them in the order they were spoken', async () => {
    // Order is the whole reason `commit` awaits one upload at a time: the
    // server appends each transcript to the entry as it lands, so a parallel
    // send would make the running order the network's decision.
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));
    const textarea = screen.getByPlaceholderText(
      'Write your journal entry...'
    ) as HTMLTextAreaElement;

    fireEvent.click(screen.getByTestId('journal-new-entry-transcribe'));
    await screen.findByText('Clip 1 · 0:42');
    fireEvent.click(screen.getByTestId('journal-new-entry-transcribe'));
    await screen.findByText('Clip 2 · 0:42');

    fireEvent.keyDown(textarea, { key: 'Enter' });

    const entryId = await waitFor(() => createMock.mock.calls[0][0].id);
    await waitFor(() =>
      expect(enqueueRecordingUpload).toHaveBeenCalledTimes(2)
    );
    expect(vi.mocked(enqueueRecordingUpload).mock.calls.map(c => c[1])).toEqual(
      ['rec-1', 'rec-2']
    );
    // Both against the one entry the composer minted, and the create says how
    // many are coming so the title waits for them.
    expect(vi.mocked(enqueueRecordingUpload).mock.calls[0][3]).toEqual({
      entryId,
    });
    expect(createMock.mock.calls[0][0].pendingAttachments).toBe(2);
  });

  it('mints the entry id at the first chunk, not at save', async () => {
    // `resumeStoredRecordings` sweeps the device at boot and uploads what it
    // finds, so a clip that has forgotten its entry is filed as a bare one of
    // its own. Minting early is what keeps a composer killed mid-thought
    // landing its clip in the right place.
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));
    const textarea = screen.getByPlaceholderText(
      'Write your journal entry...'
    ) as HTMLTextAreaElement;
    fireEvent.click(screen.getByTestId('journal-new-entry-transcribe'));
    await screen.findByText('Clip 1 · 0:42');

    const recordedAgainst = recorderStarts[0].entryId;
    expect(recordedAgainst).toEqual(expect.any(String));
    expect(enqueueRecordingUpload).not.toHaveBeenCalled();

    fireEvent.keyDown(textarea, { key: 'Enter' });

    await waitFor(() => expect(createMock).toHaveBeenCalled());
    expect(createMock.mock.calls[0][0].id).toBe(recordedAgainst);
    // Never copied into the photo store, and never POSTed directly.
    expect(storePhoto).not.toHaveBeenCalled();
    expect(uploadMock).not.toHaveBeenCalled();
  });

  it('discards the audio when a staged clip is removed by hand', async () => {
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));
    fireEvent.click(screen.getByTestId('journal-new-entry-transcribe'));
    await screen.findByText('Clip 1 · 0:42');

    fireEvent.click(screen.getByLabelText('Discard Clip 1 · 0:42'));

    expect(screen.queryByText('Clip 1 · 0:42')).toBeNull();
    // An explicit discard is one of the few places the audio may go.
    await waitFor(() => expect(deleteRecording).toHaveBeenCalledWith('rec-1'));
  });

  it('a clip alone is enough to save — no typed words needed', async () => {
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));
    fireEvent.click(screen.getByTestId('journal-new-entry-transcribe'));
    await screen.findByText('Clip 1 · 0:42');

    const save = screen.getByText('Save') as HTMLButtonElement;
    expect(save.disabled).toBe(false);
  });

  it('puts a staged file on the device and queues it, never POSTing it directly', async () => {
    // The regression this branch exists for. A staged file used to live only in
    // the composer's React state and go up on a bare fetch with no retry, so a
    // phone on spotty wifi lost the picture outright — while the recording
    // beside it came back, because recordingStore had been holding it. Now the
    // bytes are on the device before anything is attempted.
    const memo = new File(['x'], 'New Recording 4.m4a', { type: 'audio/mp4' });
    const textarea = await composeWith([memo]);

    // Shown as pending — nothing leaves while the entry is unsaved.
    expect(screen.getByText('New Recording 4.m4a')).toBeTruthy();
    expect(storePhoto).not.toHaveBeenCalled();

    fireEvent.change(textarea, { target: { value: 'a thought' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    await waitFor(() => expect(storePhoto).toHaveBeenCalledTimes(1));
    const entryId = createMock.mock.calls[0][0].id;
    const [attachmentId, file, target, targetId] =
      vi.mocked(storePhoto).mock.calls[0];
    expect(file).toBe(memo);
    expect(target).toBe('journal');
    // Against the same client-generated ULID the create used.
    expect(targetId).toBe(entryId);

    expect(enqueueJournalAttachment).toHaveBeenCalledWith(
      expect.anything(),
      attachmentId,
      entryId,
      'New Recording 4'
    );
    expect(uploadMock).not.toHaveBeenCalled();
  });

  it('tells the server how many files are coming so the title waits for them', async () => {
    const textarea = await composeWith([
      new File(['x'], 'one.png', { type: 'image/png' }),
      new File(['x'], 'two.png', { type: 'image/png' }),
    ]);

    fireEvent.change(textarea, { target: { value: 'a thought' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    await waitFor(() => expect(createMock).toHaveBeenCalled());
    expect(createMock.mock.calls[0][0].pendingAttachments).toBe(2);
    // Both queued, in the order they were pasted — the server's `position`
    // counter is what orders them, and it counts arrivals.
    await waitFor(() =>
      expect(enqueueJournalAttachment).toHaveBeenCalledTimes(2)
    );
    expect(
      vi.mocked(enqueueJournalAttachment).mock.calls.map(c => c[3])
    ).toEqual(['one', 'two']);
  });

  it('queues every staged file even when an earlier one cannot be read', async () => {
    // The old loop was one try/catch around a sequential for-await: the first
    // failure aborted the rest, which is why two pictures went at once.
    vi.mocked(storePhoto).mockRejectedValueOnce(
      new Error('Could not read that photo from this device.')
    );
    const textarea = await composeWith([
      new File(['x'], 'one.png', { type: 'image/png' }),
      new File(['x'], 'two.png', { type: 'image/png' }),
    ]);

    fireEvent.change(textarea, { target: { value: 'a thought' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    await waitFor(() => expect(storePhoto).toHaveBeenCalledTimes(2));
    expect(enqueueJournalAttachment).toHaveBeenCalledTimes(1);
    expect(vi.mocked(enqueueJournalAttachment).mock.calls[0][3]).toBe('two');
  });

  it('lets a staged file be removed before saving', async () => {
    const memo = new File(['x'], 'memo.m4a', { type: 'audio/mp4' });
    const textarea = await composeWith([memo]);

    fireEvent.click(screen.getByLabelText('Remove memo.m4a'));
    expect(screen.queryByText('memo.m4a')).toBeNull();

    fireEvent.change(textarea, { target: { value: 'a thought' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    await waitFor(() => expect(createMock).toHaveBeenCalled());
    expect(storePhoto).not.toHaveBeenCalled();
    expect(enqueueJournalAttachment).not.toHaveBeenCalled();
  });

  it('offers a photo and a file button in the compose box, staging a picked file', async () => {
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));

    expect(screen.getByText(/Photo$/)).toBeTruthy();
    expect(screen.getByText(/File$/)).toBeTruthy();

    const photo = new File(['x'], 'fence.png', { type: 'image/png' });
    const input = screen.getByTestId(
      'journal-new-entry-image-input'
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [photo] } });

    // Staged the same way a paste would be — nothing uploads until save.
    expect(screen.getByText('fence.png')).toBeTruthy();
    expect(uploadMock).not.toHaveBeenCalled();
  });

  it('stages a YouTube link and attaches it only once the entry exists', async () => {
    // The same rule staged files follow: the entry it hangs off is not there
    // yet, and the create has to land first.
    const linkMock = api.journal.attachments.link as ReturnType<typeof vi.fn>;
    linkMock.mockClear();
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));

    fireEvent.click(screen.getByTestId('journal-new-entry-link-button'));
    fireEvent.change(screen.getByTestId('journal-new-entry-link-input'), {
      target: { value: 'https://youtu.be/aircAruvnKk' },
    });
    fireEvent.click(screen.getByTestId('journal-new-entry-link-add'));

    expect(screen.getByTestId('journal-new-entry-staged-link')).toBeTruthy();
    expect(linkMock).not.toHaveBeenCalled();

    fireEvent.change(
      screen.getByPlaceholderText('Write your journal entry...'),
      {
        target: { value: 'Worth rewatching.' },
      }
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(createMock).toHaveBeenCalled());
    await waitFor(() =>
      expect(linkMock).toHaveBeenCalledWith(
        expect.any(String),
        'https://youtu.be/aircAruvnKk',
        expect.any(String)
      )
    );
  });

  it('saves an entry that is only a video link', async () => {
    // A video watched and not yet written about is a real entry, the same way
    // a photo with no words is.
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));

    fireEvent.click(screen.getByTestId('journal-new-entry-link-button'));
    fireEvent.change(screen.getByTestId('journal-new-entry-link-input'), {
      target: { value: 'https://youtu.be/aircAruvnKk' },
    });
    fireEvent.click(screen.getByTestId('journal-new-entry-link-add'));

    const save = screen.getByRole('button', {
      name: 'Save',
    }) as HTMLButtonElement;
    expect(save.disabled).toBe(false);
  });

  it('takes several photos at once, unlike the single-file buttons it replaced', async () => {
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));

    const input = screen.getByTestId(
      'journal-new-entry-image-input'
    ) as HTMLInputElement;
    expect(input.multiple).toBe(true);

    fireEvent.change(input, {
      target: {
        files: [
          new File(['x'], 'one.png', { type: 'image/png' }),
          new File(['x'], 'two.png', { type: 'image/png' }),
        ],
      },
    });

    expect(screen.getByText('one.png')).toBeTruthy();
    expect(screen.getByText('two.png')).toBeTruthy();
  });

  it('the file button accepts anything', async () => {
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));

    const input = screen.getByTestId(
      'journal-new-entry-file-input'
    ) as HTMLInputElement;
    // No `accept`: the backend stores what the media tables don't claim as
    // kind='file' rather than refusing it.
    expect(input.getAttribute('accept')).toBeNull();

    fireEvent.change(input, {
      target: {
        files: [new File(['x'], 'taxes.pdf', { type: 'application/pdf' })],
      },
    });
    expect(screen.getByText('taxes.pdf')).toBeTruthy();
  });

  it('hides the camera button on a device with a mouse', async () => {
    // `capture` opens the camera on a phone and is ignored everywhere else, so
    // on a desktop the button would be a second file dialog with a camera icon.
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));

    expect(screen.queryByText(/Take a photo/)).toBeNull();
    expect(screen.queryByTestId('journal-new-entry-camera-input')).toBeNull();
  });

  it('offers the camera on a touch device', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: q === '(pointer: coarse)',
      media: q,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    try {
      renderJournal();
      fireEvent.click(await screen.findByText('+ New Entry'));

      expect(screen.getByText(/Take a photo/)).toBeTruthy();
      const camera = screen.getByTestId(
        'journal-new-entry-camera-input'
      ) as HTMLInputElement;
      expect(camera.getAttribute('capture')).toBe('environment');
      expect(camera.accept).toBe('image/*');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('tells the server how many attachments are coming, so the title waits', async () => {
    // Attachments can only be uploaded once the entry exists. Without this the
    // title is generated from the text alone, before any photo is captioned —
    // which is why a photo never influenced a title.
    const textarea = await composeWith([
      new File(['x'], 'fence.png', { type: 'image/png' }),
    ]);
    fireEvent.change(textarea, { target: { value: 'look at this' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(
        expect.objectContaining({ pendingAttachments: 1 })
      )
    );
  });

  it('saves a photo-only entry, which has no words to title it from', async () => {
    const textarea = await composeWith([
      new File(['x'], 'fence.png', { type: 'image/png' }),
    ]);
    // No text typed at all.
    const save = screen.getByText('Save') as HTMLButtonElement;
    expect(save.disabled).toBe(false);
    fireEvent.click(save);

    await waitFor(() => expect(createMock).toHaveBeenCalled());
    expect(textarea).toBeTruthy();
  });

  it('reports a file it could not read without implying the entry was lost', async () => {
    // The only attachment failure the user can still act on. A failed *upload*
    // is no longer one of them — it is queued, and it retries — so this is
    // about a file whose bytes never reached the device at all.
    vi.mocked(storePhoto).mockRejectedValue(
      new Error('That photo came back empty')
    );
    const textarea = await composeWith([
      new File(['x'], 'big.mov', { type: 'video/quicktime' }),
    ]);

    fireEvent.change(textarea, { target: { value: 'a thought' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    expect(
      await screen.findByText(
        /The entry was saved, but "big.mov" could not be attached/
      )
    ).toBeTruthy();
  });
});

// A voice-only entry (nothing but a single recording) offers to fold itself
// into another entry from the same day — see isVoiceOnlyEntry and
// backend/routes/journal.py's merge route.
describe('Journal merge picker', () => {
  const listMock = api.journal.list as ReturnType<typeof vi.fn>;
  const mergeCandidatesMock = api.journal.mergeCandidates as ReturnType<
    typeof vi.fn
  >;
  const mergeMock = api.journal.merge as ReturnType<typeof vi.fn>;

  const voiceOnlyEntry: JournalEntry = {
    id: 'e-voice',
    content: '',
    rawContent: null,
    title: null,
    tags: null,
    curatedTags: [],
    ficRefs: [],
    attachments: [
      {
        id: 'a1',
        entryId: 'e-voice',
        kind: 'audio',
        name: 'Recording',
        url: '/api/journal/attachments/a1/file',
        mime: 'audio/mp4',
        size: 1024,
        position: 0,
        transcript: null,
        transcriptStatus: 'idle',
        transcriptError: null,
        description: null,
        descriptionStatus: 'idle',
        descriptionError: null,
        latitude: null,
        longitude: null,
        createdAt: '2026-07-02T10:00:00Z',
      },
    ],
    createdAt: '2026-07-02T10:00:00Z',
    updatedAt: '',
  };

  beforeEach(() => {
    vi.stubGlobal('EventSource', FakeEventSource);
    Element.prototype.scrollIntoView = vi.fn();
    listMock.mockResolvedValue([voiceOnlyEntry]);
    mergeCandidatesMock.mockReset();
    mergeMock.mockReset();
  });

  afterEach(() => listMock.mockResolvedValue(ENTRIES));

  async function openVoiceOnlyEntryForEdit() {
    renderJournal();
    fireEvent.click(await screen.findByText('Edit'));
  }

  it('offers to merge when another entry exists from the same day', async () => {
    mergeCandidatesMock.mockResolvedValue([
      { ...ENTRIES[0], id: 'e-same-day', content: 'Notes from lunch.' },
    ]);
    await openVoiceOnlyEntryForEdit();

    expect(
      await screen.findByText(
        'Just a recording — attach it to another entry from today instead?'
      )
    ).toBeTruthy();
    expect(mergeCandidatesMock).toHaveBeenCalledWith('e-voice');
  });

  it('stays silent when there are no same-day candidates', async () => {
    mergeCandidatesMock.mockResolvedValue([]);
    await openVoiceOnlyEntryForEdit();

    await waitFor(() => expect(mergeCandidatesMock).toHaveBeenCalled());
    expect(
      screen.queryByText(
        'Just a recording — attach it to another entry from today instead?'
      )
    ).toBeNull();
  });

  it('shows an error rather than silently disappearing when the check fails', async () => {
    // E.g. a backend process that hasn't picked up the merge route yet — that
    // used to look identical to "no candidates today" (both rendered nothing).
    mergeCandidatesMock.mockRejectedValue(new Error('HTTP 404'));
    await openVoiceOnlyEntryForEdit();

    expect(
      await screen.findByText(/Couldn't check for other entries to merge into/)
    ).toBeTruthy();
  });

  it('merges into the chosen entry', async () => {
    mergeCandidatesMock.mockResolvedValue([
      { ...ENTRIES[0], id: 'e-same-day', content: 'Notes from lunch.' },
    ]);
    mergeMock.mockResolvedValue({ ...ENTRIES[0], id: 'e-same-day' });
    await openVoiceOnlyEntryForEdit();
    await screen.findByText(
      'Just a recording — attach it to another entry from today instead?'
    );

    fireEvent.change(screen.getByLabelText('Entry to merge into'), {
      target: { value: 'e-same-day' },
    });
    fireEvent.click(screen.getByText('Merge'));

    await waitFor(() =>
      expect(mergeMock).toHaveBeenCalledWith('e-voice', 'e-same-day')
    );
  });
});

describe('Journal voice drafts panel', () => {
  const listVoiceDraftsMock = api.journal.voiceDrafts.list as ReturnType<
    typeof vi.fn
  >;
  const retryVoiceDraftMock = api.journal.voiceDrafts.retry as ReturnType<
    typeof vi.fn
  >;
  const deleteVoiceDraftMock = api.journal.voiceDrafts.delete as ReturnType<
    typeof vi.fn
  >;

  beforeEach(() => {
    vi.stubGlobal('EventSource', FakeEventSource);
    Element.prototype.scrollIntoView = vi.fn();
    listVoiceDraftsMock.mockReset();
    retryVoiceDraftMock.mockReset();
    deleteVoiceDraftMock.mockReset();
  });

  afterEach(() => listVoiceDraftsMock.mockResolvedValue([]));

  it('stays out of the way when there are no drafts', async () => {
    listVoiceDraftsMock.mockResolvedValue([]);
    renderJournal();
    await screen.findByText('First entry');

    expect(screen.queryByText(/Voice drafts/)).toBeNull();
  });

  it('shows a processing draft with no retry/discard controls', async () => {
    listVoiceDraftsMock.mockResolvedValue([
      {
        id: 'd1',
        url: '/api/journal/voice-drafts/d1/file',
        mime: 'audio/wav',
        size: 2048,
        status: 'processing',
        error: null,
        candidates: [],
        entryId: null,
        createdAt: '2026-07-02T10:00:00Z',
        completedAt: null,
      },
    ]);
    renderJournal();

    expect(await screen.findByText(/Voice drafts · 1 processing/)).toBeTruthy();
    expect(screen.queryByText('Retry')).toBeNull();
    expect(screen.queryByText('Discard')).toBeNull();
  });

  it('offers retry and discard for an errored draft, and retrying refetches the list', async () => {
    listVoiceDraftsMock.mockResolvedValueOnce([
      {
        id: 'd2',
        url: '/api/journal/voice-drafts/d2/file',
        mime: 'audio/wav',
        size: 2048,
        status: 'error',
        error: 'All STT backends failed',
        candidates: [],
        entryId: null,
        createdAt: '2026-07-02T10:00:00Z',
        completedAt: null,
      },
    ]);
    retryVoiceDraftMock.mockResolvedValue({ success: true });
    renderJournal();

    await screen.findByText(/Voice drafts · 1 failed/);
    expect(screen.getByText('All STT backends failed')).toBeTruthy();

    listVoiceDraftsMock.mockResolvedValueOnce([]);
    fireEvent.click(screen.getByText('Retry'));

    await waitFor(() => expect(retryVoiceDraftMock).toHaveBeenCalledWith('d2'));
    await waitFor(() => expect(screen.queryByText(/Voice drafts/)).toBeNull());
  });

  it('discarding a draft removes it from the panel', async () => {
    listVoiceDraftsMock.mockResolvedValueOnce([
      {
        id: 'd3',
        url: '/api/journal/voice-drafts/d3/file',
        mime: 'audio/wav',
        size: 2048,
        status: 'error',
        error: 'boom',
        candidates: [],
        entryId: null,
        createdAt: '2026-07-02T10:00:00Z',
        completedAt: null,
      },
    ]);
    deleteVoiceDraftMock.mockResolvedValue({ success: true });
    renderJournal();
    await screen.findByText(/Voice drafts · 1 failed/);

    listVoiceDraftsMock.mockResolvedValueOnce([]);
    fireEvent.click(screen.getByText('Discard'));

    await waitFor(() =>
      expect(deleteVoiceDraftMock).toHaveBeenCalledWith('d3')
    );
    await waitFor(() => expect(screen.queryByText(/Voice drafts/)).toBeNull());
  });
});

describe('an entry that was recorded as an idea', () => {
  const listMock = api.journal.list as ReturnType<typeof vi.fn>;

  // The Ideas tab's Record button files one clip as two rows. From this side
  // the entry is an ordinary journal entry that happens to know which idea it
  // became — and stops knowing the moment that idea is deleted, which is why
  // the pill is drawn off `ideaId` rather than off the presence of a title.
  const withIdea = (over: Partial<JournalEntry> = {}) => [
    {
      ...ENTRIES[0]!,
      content: 'A grid of habits in the day view.',
      ideaId: 'i9',
      ideaTitle: 'Habit grid',
      ...over,
    },
  ];

  afterEach(() => listMock.mockResolvedValue(ENTRIES));

  it('offers a link into the idea', async () => {
    listMock.mockResolvedValue(withIdea());
    const onOpenIdea = vi.fn();
    renderJournal({ onOpenIdea });

    fireEvent.click(await screen.findByText('💡 Habit grid'));
    expect(onOpenIdea).toHaveBeenCalledWith({ ideaId: 'i9' });
  });

  it('names an idea that has not been titled yet', async () => {
    listMock.mockResolvedValue(withIdea({ ideaTitle: null }));
    renderJournal();
    expect(await screen.findByText('💡 Untitled idea')).toBeTruthy();
  });

  it('shows no link once the idea is deleted', async () => {
    listMock.mockResolvedValue(withIdea({ ideaId: null, ideaTitle: null }));
    renderJournal();

    // The entry and its recording survive the idea; only the pill goes.
    expect(
      await screen.findByText('A grid of habits in the day view.')
    ).toBeTruthy();
    expect(screen.queryByText(/💡/)).toBeNull();
  });

  it('scrolls to the entry the Ideas tab linked back to', async () => {
    listMock.mockResolvedValue([...withIdea(), ENTRIES[1]!]);
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;

    renderJournal({ target: { entryId: 'e1' }, onTargetConsumed: vi.fn() });
    await screen.findByText('A grid of habits in the day view.');

    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
  });
});

describe('archived newspapers in the feed', () => {
  const issue = {
    date: '2026-07-02',
    archivedAt: '2026-07-02T06:00:00Z',
    byteSize: 1000,
    pageCount: 40,
    markedPages: 0,
    pdfUrl: '/api/newspapers/issues/2026-07-02/pdf',
  };

  it('shows how much of the paper has been marked up', async () => {
    vi.mocked(api.newspapers.journalIssues).mockResolvedValue([
      { ...issue, markedPages: 7 },
    ]);
    renderJournal();
    expect(await screen.findByText(/7 of 40 pages marked up/)).toBeTruthy();
  });

  it('still lists an issue that was never opened', async () => {
    vi.mocked(api.newspapers.journalIssues).mockResolvedValue([issue]);
    renderJournal();
    expect(await screen.findByText(/40 pages · not marked up/)).toBeTruthy();
  });

  it('keeps the card out of a search, where the feed is entries only', async () => {
    vi.mocked(api.newspapers.journalIssues).mockResolvedValue([issue]);
    renderJournal();
    await screen.findByText(/not marked up/);
    fireEvent.change(screen.getByPlaceholderText(/Search/i), {
      target: { value: 'first' },
    });
    await waitFor(() => expect(screen.queryByText(/not marked up/)).toBeNull());
  });
});

describe('filed study sources in the feed', () => {
  const card = {
    id: 'src-1',
    title: 'Transformers, lecture 3',
    kind: 'youtube' as const,
    sourceUrl: 'https://www.youtube.com/watch?v=abc',
    durationSeconds: 1680,
    journalDate: '2026-07-02',
    archivedAt: '2026-07-02T20:00:00Z',
    fileUrl: '/api/study/sources/src-1/file',
    pages: [
      { id: 'pg-1', imageUrl: '/api/paper/pages/pg-1/image?v=1' },
      { id: 'pg-2', imageUrl: '/api/paper/pages/pg-2/image?v=1' },
    ],
    notePath: 'study/transformers.md',
    note: 'Q, K and V are three projections of the same input.',
    noteTruncated: false,
  };

  it('shows the media, the pages and the note in one card', async () => {
    // One sitting, one card: reading the source and writing the page beside it
    // are not two events in the day's record.
    vi.mocked(api.study.journal).mockResolvedValue([card]);
    renderJournal();

    expect(await screen.findByText(/Transformers, lecture 3/)).toBeTruthy();
    expect(
      screen.getByText('https://www.youtube.com/watch?v=abc')
    ).toBeTruthy();
    expect(screen.getByText(/Q, K and V are three projections/)).toBeTruthy();
    expect(
      document.querySelectorAll('img[src^="/api/paper/pages/"]')
    ).toHaveLength(2);
  });

  it('renders a source studied with no paper, without claiming pages are missing', async () => {
    vi.mocked(api.study.journal).mockResolvedValue([{ ...card, pages: [] }]);
    renderJournal();

    expect(await screen.findByText(/Transformers, lecture 3/)).toBeTruthy();
    // "No pages" is the paper card's wording, and it would read here as
    // something lost rather than something never made.
    expect(screen.queryByText('No pages')).toBeNull();
  });

  it('says when a long note was cut short', async () => {
    vi.mocked(api.study.journal).mockResolvedValue([
      { ...card, noteTruncated: true },
    ]);
    renderJournal();
    expect(await screen.findByText(/note truncated/)).toBeTruthy();
  });

  it('says when the archive drive is out', async () => {
    vi.mocked(api.study.journal).mockResolvedValue([
      {
        ...card,
        fileAvailable: false,
        fileUnavailableReason: 'The archive drive is not connected.',
      },
    ]);
    renderJournal();
    expect(
      await screen.findByText('The archive drive is not connected.')
    ).toBeTruthy();
  });

  it('keeps the card out of a search, where the feed is entries only', async () => {
    vi.mocked(api.study.journal).mockResolvedValue([card]);
    renderJournal();
    await screen.findByText(/Transformers, lecture 3/);
    fireEvent.change(screen.getByPlaceholderText(/Search/i), {
      target: { value: 'first' },
    });
    await waitFor(() =>
      expect(screen.queryByText(/Transformers, lecture 3/)).toBeNull()
    );
  });
});
