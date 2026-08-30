// @vitest-environment jsdom
/**
 * The regression test for "typing a journal entry on the iPhone is laggy".
 *
 * The cause was structural, not a slow function: `newEntry` was `useState` on
 * the top-level `Journal` component, so React re-ran the whole component body
 * on every keystroke and re-rendered the entire feed with it — rebuilding the
 * six-way merged feed, recomputing event-group spans (O(events x feed), with a
 * Date parse per item), re-parsing every entry's tags, constructing a fresh
 * `Intl.DateTimeFormat` per row, and detaching/re-attaching every row's callback
 * ref. Each of those was fixed, but the fix that matters is that none of them
 * are on the typing path any more.
 *
 * So this asserts the structure rather than a duration: a wall-clock budget
 * would be flaky on CI and would not fail for the right reason. If the composer
 * state ever moves back onto `Journal`, the render counter below goes up and
 * this test says so.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Journal } from './Journal';
import { ShortcutProvider } from '../shortcuts/ShortcutProvider';
import type { JournalEntry } from '../hooks/api';

const { ENTRIES, renders } = vi.hoisted(() => {
  const ENTRIES: JournalEntry[] = Array.from({ length: 30 }, (_, i) => ({
    id: `e${i}`,
    content: `Entry ${i}`,
    rawContent: null,
    title: null,
    tags: '["work"]',
    curatedTags: [],
    ficRefs: [],
    createdAt: new Date(Date.UTC(2026, 6, 1, 10, i)).toISOString(),
    updatedAt: '',
  }));
  return { ENTRIES, renders: { attachments: 0 } };
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

// The per-row child stands in for the whole feed: it is mounted once per entry
// and counting its renders counts the feed's.
vi.mock('./JournalAttachments', () => ({
  JournalAttachments: () => {
    renders.attachments++;
    return null;
  },
}));

vi.mock('../hooks/useRecorder', () => ({
  useRecorder: () => ({
    status: 'idle',
    canTranscribe: true,
    error: '',
    start: vi.fn(),
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

describe('typing in the compose box does not re-render the feed', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', FakeEventSource);
    Element.prototype.scrollIntoView = vi.fn();
    renders.attachments = 0;
  });

  it('renders no feed rows again while the draft is being typed', async () => {
    renderJournal();
    await screen.findByText('Entry 0');
    fireEvent.click(screen.getByText('+ New Entry'));

    const textarea = screen.getByPlaceholderText(
      'Write your journal entry...'
    ) as HTMLTextAreaElement;

    // Opening the box does re-render Journal once (showNewEntry lives there).
    // What must not happen is a re-render per character after that.
    renders.attachments = 0;
    for (const value of ['a', 'a t', 'a tho', 'a thought']) {
      fireEvent.change(textarea, { target: { value } });
    }

    expect(textarea.value).toBe('a thought');
    expect(renders.attachments).toBe(0);

    // And the discriminating half: typing into a box whose state *is* on
    // `Journal` does re-render the rows. That is what the compose box used to
    // do on every character, and it is what this counter would have caught.
    fireEvent.change(screen.getByPlaceholderText('Search entries...'), {
      target: { value: 'x' },
    });
    expect(renders.attachments).toBeGreaterThan(0);
  });

  it('counts a render when there really is one', async () => {
    // Without this the test above would pass just as happily if the rows had
    // stopped rendering altogether, or if the mock were never wired up.
    renderJournal();
    await screen.findByText('Entry 0');

    expect(renders.attachments).toBeGreaterThanOrEqual(ENTRIES.length);
  });
});
