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
    },
    curatedTags: { list: vi.fn().mockResolvedValue([]) },
    transcriptions: { list: vi.fn().mockResolvedValue([]), delete: vi.fn() },
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
  },
}));

// The edit-mode dictation button. Only the transcript matters here — the
// microphone plumbing is covered by useRecorder's own test.
vi.mock('../hooks/useRecorder', () => ({
  useRecorder: (onTranscript: (text: string) => void) => ({
    status: 'idle',
    error: '',
    start: vi.fn(async () => onTranscript('and one more thing')),
    stop: vi.fn(),
  }),
}));

class FakeEventSource {
  onmessage: unknown = null;
  close() {}
}

function renderJournal() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="journal" onViewChange={() => {}}>
        <Journal />
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

describe('Journal edit-mode dictation', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', FakeEventSource);
    Element.prototype.scrollIntoView = vi.fn();
  });

  it('appends the transcript to the entry being edited rather than replacing it', async () => {
    renderJournal();
    await screen.findByText('First entry');
    openEditWithKeyboard();

    // Held by reference: getByDisplayValue collapses the newline the
    // transcript is appended on, so the assertion reads the value directly.
    const textarea = screen.getByDisplayValue(
      'First entry'
    ) as HTMLTextAreaElement;
    fireEvent.click(screen.getByLabelText('Dictate into this entry'));

    await waitFor(() =>
      expect(textarea.value).toBe('First entry\nand one more thing')
    );
    // Dictating is not saving — the entry only changes when Save is pressed.
    expect(api.journal.update).not.toHaveBeenCalled();
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

  it('offers Add audio/photo buttons in the compose box, staging a picked file', async () => {
    renderJournal();
    fireEvent.click(await screen.findByText('+ New Entry'));

    expect(screen.getByText('Add audio or video')).toBeTruthy();
    expect(screen.getByText('Add photo')).toBeTruthy();

    const photo = new File(['x'], 'fence.png', { type: 'image/png' });
    const input = screen.getByTestId(
      'journal-new-entry-image-input'
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [photo] } });

    // Staged the same way a paste would be — nothing uploads until save.
    expect(screen.getByText('fence.png')).toBeTruthy();
    expect(uploadMock).not.toHaveBeenCalled();
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
