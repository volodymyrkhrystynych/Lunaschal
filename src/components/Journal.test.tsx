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
import {
  attachRecordingToEntry,
  handleFinishedRecording,
} from '../offline/recordingQueue';
import { deleteRecording } from '../offline/recordingStore';

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
      },
      voiceDrafts: {
        list: vi.fn().mockResolvedValue([]),
        retry: vi.fn(),
        delete: vi.fn(),
      },
    },
    curatedTags: { list: vi.fn().mockResolvedValue([]) },
    transcriptions: { list: vi.fn().mockResolvedValue([]), delete: vi.fn() },
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
  },
}));

// The Transcribe buttons. The microphone plumbing is covered by useRecorder's
// own test; what matters here is that pressing one delivers *both* halves — the
// text to the textarea and the stored recording to whatever keeps the audio.
// `start` does both, so a test can drive the whole thing by clicking the real
// button and never has to reach for a particular hook instance (there are two
// live at once once the composer is open).
const STORED_RECORDING = { id: 'rec-1', mimeType: 'audio/webm' };

vi.mock('../hooks/useRecorder', () => ({
  useRecorder: (
    onTranscript: (text: string) => void,
    _onAudio: unknown,
    options: {
      onRecording?: (rec: typeof STORED_RECORDING) => void | Promise<void>;
    } = {}
  ) => ({
    status: 'idle',
    canTranscribe: true,
    error: '',
    start: vi.fn(async () => {
      onTranscript('and one more thing');
      await options.onRecording?.(STORED_RECORDING);
    }),
    stop: vi.fn(),
  }),
}));

vi.mock('../offline/recordingQueue', () => ({
  attachRecordingToEntry: vi.fn().mockResolvedValue(undefined),
  handleFinishedRecording: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../offline/recordingStore', () => ({
  assembleBlob: vi.fn(async () => new Blob(['audio'], { type: 'audio/webm' })),
  deleteRecording: vi.fn().mockResolvedValue(undefined),
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

describe('Journal edit-mode Transcribe', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.journal.list).mockResolvedValue(ENTRIES);
    vi.stubGlobal('EventSource', FakeEventSource);
    Element.prototype.scrollIntoView = vi.fn();
  });

  const pressTranscribe = () =>
    fireEvent.click(screen.getByLabelText('Transcribe into this entry'));

  it('appends the transcript to the entry being edited rather than replacing it', async () => {
    renderJournal();
    await screen.findByText('First entry');
    openEditWithKeyboard();

    // Held by reference: getByDisplayValue collapses the newline the
    // transcript is appended on, so the assertion reads the value directly.
    const textarea = screen.getByDisplayValue(
      'First entry'
    ) as HTMLTextAreaElement;
    pressTranscribe();

    await waitFor(() =>
      expect(textarea.value).toBe('First entry\nand one more thing')
    );
    // Dictating is not saving — the entry only changes when Save is pressed.
    expect(api.journal.update).not.toHaveBeenCalled();
  });

  it('keeps the audio, attaching it to the entry being edited', async () => {
    // The point of the rename. It used to transcribe and drop the recording;
    // now the clip is kept as an attachment on that same entry.
    renderJournal();
    await screen.findByText('First entry');
    openEditWithKeyboard();

    pressTranscribe();

    await waitFor(() =>
      expect(attachRecordingToEntry).toHaveBeenCalledWith(
        expect.anything(),
        STORED_RECORDING,
        'e1'
      )
    );
    // Not the make-a-new-entry policy — that is for a recording with no entry
    // to belong to.
    expect(handleFinishedRecording).not.toHaveBeenCalled();
  });

  it('reads Transcribe, not Record', async () => {
    renderJournal();
    await screen.findByText('First entry');
    openEditWithKeyboard();

    expect(screen.getByText('● Transcribe')).toBeTruthy();
    expect(screen.queryByText('● Record')).toBeNull();
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
    vi.mocked(deleteRecording).mockClear();
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

  it('offers Transcribe in the compose box too', async () => {
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));

    expect(screen.getByTestId('journal-new-entry-transcribe')).toBeTruthy();
    expect(screen.getByText('● Transcribe')).toBeTruthy();
  });

  it('puts the transcript in the draft and stages the recording alongside it', async () => {
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));
    const textarea = screen.getByPlaceholderText(
      'Write your journal entry...'
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'a thought' } });

    fireEvent.click(screen.getByTestId('journal-new-entry-transcribe'));

    // The text lands in the box the user is typing in — there is no entry yet
    // for a server-side transcript to be written into.
    await waitFor(() =>
      expect(textarea.value).toBe('a thought\nand one more thing')
    );
    // …and the audio is staged like any other attachment, named after what
    // MediaRecorder actually produced.
    expect(await screen.findByText('recording.webm')).toBeTruthy();
    expect(uploadMock).not.toHaveBeenCalled();
  });

  it('uploads the recorded audio on save and only then lets go of it', async () => {
    // The invariant this whole durable path exists for: the IndexedDB copy is
    // released after the server confirms the attachment, never before.
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));
    const textarea = screen.getByPlaceholderText(
      'Write your journal entry...'
    ) as HTMLTextAreaElement;
    fireEvent.click(screen.getByTestId('journal-new-entry-transcribe'));
    await screen.findByText('recording.webm');
    expect(deleteRecording).not.toHaveBeenCalled();

    fireEvent.keyDown(textarea, { key: 'Enter' });

    await waitFor(() => expect(uploadMock).toHaveBeenCalledTimes(1));
    const [, file] = uploadMock.mock.calls[0];
    expect((file as File).name).toBe('recording.webm');
    await waitFor(() => expect(deleteRecording).toHaveBeenCalledWith('rec-1'));
  });

  it('discards the audio when a staged recording is removed by hand', async () => {
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));
    fireEvent.click(screen.getByTestId('journal-new-entry-transcribe'));
    await screen.findByText('recording.webm');

    fireEvent.click(screen.getByLabelText('Remove recording.webm'));

    expect(screen.queryByText('recording.webm')).toBeNull();
    // An explicit discard is one of the few places the audio may go.
    await waitFor(() => expect(deleteRecording).toHaveBeenCalledWith('rec-1'));
  });

  it('a recording alone is enough to save — no typed words needed', async () => {
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));
    fireEvent.click(screen.getByTestId('journal-new-entry-transcribe'));
    await screen.findByText('recording.webm');

    const save = screen.getByText('Save') as HTMLButtonElement;
    expect(save.disabled).toBe(false);
  });

  it('stages a pasted file and uploads it after the entry saves', async () => {
    const memo = new File(['x'], 'New Recording 4.m4a', { type: 'audio/mp4' });
    const textarea = await composeWith([memo]);

    // Shown as pending — nothing is uploaded while the entry is unsaved.
    expect(screen.getByText('New Recording 4.m4a')).toBeTruthy();
    expect(uploadMock).not.toHaveBeenCalled();

    fireEvent.change(textarea, { target: { value: 'a thought' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    await waitFor(() => expect(uploadMock).toHaveBeenCalledTimes(1));
    const [entryId, file, name] = uploadMock.mock.calls[0];
    expect(file).toBe(memo);
    expect(name).toBe('New Recording 4');
    // Uploaded against the same client-generated ULID the create used.
    expect(entryId).toBe(createMock.mock.calls[0][0].id);
  });

  it('lets a staged file be removed before saving', async () => {
    const memo = new File(['x'], 'memo.m4a', { type: 'audio/mp4' });
    const textarea = await composeWith([memo]);

    fireEvent.click(screen.getByLabelText('Remove memo.m4a'));
    expect(screen.queryByText('memo.m4a')).toBeNull();

    fireEvent.change(textarea, { target: { value: 'a thought' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    await waitFor(() => expect(createMock).toHaveBeenCalled());
    expect(uploadMock).not.toHaveBeenCalled();
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

  it('reports a failed attachment upload without implying the entry was lost', async () => {
    uploadMock.mockRejectedValue(new Error('file is too large'));
    const textarea = await composeWith([
      new File(['x'], 'big.mov', { type: 'video/quicktime' }),
    ]);

    fireEvent.change(textarea, { target: { value: 'a thought' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    expect(
      await screen.findByText(/The entry was saved, but its attachment failed/)
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
