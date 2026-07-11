// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Journal } from './Journal';
import { ShortcutProvider } from '../shortcuts/ShortcutProvider';
import type { JournalEntry } from '../hooks/api';

const { ENTRIES } = vi.hoisted(() => {
  const ENTRIES: JournalEntry[] = [
    {
      id: 'e1', content: 'First entry', rawContent: null, title: null, tags: null,
      curatedTags: [], ficRefs: [], createdAt: '2026-07-02T10:00:00Z', updatedAt: '',
    },
    {
      id: 'e2', content: 'Second entry', rawContent: null, title: null, tags: null,
      curatedTags: [], ficRefs: [], createdAt: '2026-07-01T10:00:00Z', updatedAt: '',
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
    flashcard: { generateFromJournal: vi.fn() },
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
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
    </QueryClientProvider>,
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
    fireEvent.keyDown(screen.getByDisplayValue('First entry'), { key: 'Escape' });

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
