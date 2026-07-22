// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
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
    },
    curatedTags: { list: vi.fn().mockResolvedValue([]) },
    transcriptions: { list: vi.fn().mockResolvedValue([]), delete: vi.fn() },
    learning: { generateFromJournal: vi.fn() },
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
  },
}));

class FakeEventSource {
  onmessage: unknown = null;
  close() {}
}

function renderJournal() {
  const queryClient = new QueryClient();
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
