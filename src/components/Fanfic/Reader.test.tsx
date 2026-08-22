// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Reader } from './Reader';
import { ShortcutProvider } from '../../shortcuts/ShortcutProvider';
import { api } from '../../hooks/api';
import type {
  Fic,
  FicBookmark,
  FicChapter,
  FicChapterSummary,
} from '../../hooks/api';

const { CHAPTERS, FIC } = vi.hoisted(() => {
  const CHAPTERS: FicChapterSummary[] = [
    {
      id: 'ch1',
      ficId: 'fic1',
      position: 1,
      title: 'Chapter 1',
      category: 'Chapters',
      wordCount: 100,
      postedAt: null,
      isRead: false,
    },
    {
      id: 'ch2',
      ficId: 'fic1',
      position: 2,
      title: 'Chapter 2',
      category: 'Chapters',
      wordCount: 100,
      postedAt: null,
      isRead: false,
    },
    {
      id: 'ch3',
      ficId: 'fic1',
      position: 3,
      title: 'Chapter 3',
      category: 'Chapters',
      wordCount: 100,
      postedAt: null,
      isRead: false,
    },
  ];
  const FIC: Fic = {
    id: 'fic1',
    title: 'Test Fic',
    author: 'Author',
    sourceType: 'xenforo',
    sourceUrl: null,
    site: null,
    description: null,
    coverPath: null,
    wordCount: 300,
    chapterCount: 3,
    downloadStatus: 'complete',
    downloadError: null,
    lastReadChapterId: null,
    lastCheckedAt: null,
    rating: null,
    createdAt: '',
    updatedAt: '',
    folderIds: [],
  };
  return { CHAPTERS, FIC };
});

vi.mock('../../hooks/api', () => ({
  api: {
    fanfic: {
      get: vi.fn().mockResolvedValue(FIC),
      chapters: vi.fn().mockResolvedValue(CHAPTERS),
      chapter: vi.fn().mockImplementation((id: string) => {
        const summary = CHAPTERS.find(c => c.id === id)!;
        const chapter: FicChapter = {
          ...summary,
          contentHtml: '<p>text</p>',
          contentText: 'text',
          sourceUrl: null,
          createdAt: '',
        };
        return Promise.resolve(chapter);
      }),
      saveProgress: vi.fn().mockResolvedValue({ success: true }),
      setRead: vi.fn().mockResolvedValue({ success: true, readCount: 0 }),
      bookmarks: {
        list: vi.fn().mockResolvedValue([]),
        create: vi.fn(),
        delete: vi.fn(),
      },
      folders: {
        list: vi.fn().mockResolvedValue([]),
      },
      addToFolder: vi.fn().mockResolvedValue({ success: true }),
      removeFromFolder: vi.fn().mockResolvedValue({ success: true }),
      linkJournal: vi.fn().mockResolvedValue({ success: true }),
    },
    journal: {
      createFromVoice: vi.fn().mockResolvedValue({ id: 'j1' }),
    },
    shortcuts: {
      get: vi.fn().mockResolvedValue({ bindings: {} }),
    },
    settings: { get: vi.fn().mockResolvedValue({}) },
    auth: {
      status: vi
        .fn()
        .mockResolvedValue({ authenticated: true, networkMode: false }),
    },
  },
}));

function renderReader(onBack: () => void = () => {}) {
  // Matches main.tsx's mutation default: without it, TanStack Query's own
  // 'online' default pauses (rather than fires) any mutation not opted into
  // the offline queue, since jsdom has no real network to report as up.
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { networkMode: 'always' } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="fanfic" onViewChange={() => {}}>
        <Reader ficId="fic1" onBack={onBack} />
      </ShortcutProvider>
    </QueryClientProvider>
  );
}

describe('Reader chapter sidebar', () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.scrollTo = vi.fn();
  });

  it('scrolls the newly selected chapter into view so it stays visible', async () => {
    renderReader();
    await screen.findByText('Chapter 1');

    const scrollSpy = Element.prototype.scrollIntoView as unknown as ReturnType<
      typeof vi.fn
    >;
    const callsBefore = scrollSpy.mock.calls.length;

    fireEvent.click(screen.getByText('Chapter 3'));

    await waitFor(() => {
      expect(scrollSpy.mock.calls.length).toBeGreaterThan(callsBefore);
    });
    expect(scrollSpy.mock.calls.at(-1)?.[0]).toEqual({ block: 'nearest' });
  });
});

describe('Reader keyboard navigation', () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.scrollTo = vi.fn();
    Element.prototype.scrollBy = vi.fn();
  });

  const heading = (name: string) => screen.findByRole('heading', { name });

  // In tests the provider mounts together with the Reader, so its level-reset
  // effect wins over the Reader's setLevel(1); press D once to descend into
  // the chapter list, as from the sidebar level.
  const enterChapterList = () => fireEvent.keyDown(window, { code: 'KeyD' });

  it('W/S switch chapters at the chapter-list level', async () => {
    renderReader();
    await heading('Chapter 1');
    enterChapterList();

    fireEvent.keyDown(window, { code: 'KeyS' });
    await heading('Chapter 2');

    fireEvent.keyDown(window, { code: 'KeyW' });
    await heading('Chapter 1');
  });

  it('D enters the chapter; W/S then scroll the prose without changing chapters', async () => {
    renderReader();
    await heading('Chapter 1');
    enterChapterList();
    const scrollSpy = Element.prototype.scrollBy as unknown as ReturnType<
      typeof vi.fn
    >;

    fireEvent.keyDown(window, { code: 'KeyD' });
    fireEvent.keyDown(window, { code: 'KeyS' });
    expect(scrollSpy).toHaveBeenCalledWith({ top: 120, behavior: 'smooth' });

    fireEvent.keyDown(window, { code: 'KeyW' });
    expect(scrollSpy).toHaveBeenCalledWith({ top: -120, behavior: 'smooth' });

    await heading('Chapter 1'); // still on the same chapter
  });

  it('A backs out of reading to the chapter list, then to the library', async () => {
    const onBack = vi.fn();
    renderReader(onBack);
    await heading('Chapter 1');
    enterChapterList();

    fireEvent.keyDown(window, { code: 'KeyD' }); // enter chapter
    fireEvent.keyDown(window, { code: 'KeyA' }); // back to chapter list
    expect(onBack).not.toHaveBeenCalled();

    fireEvent.keyDown(window, { code: 'KeyS' }); // W/S switch chapters again
    await heading('Chapter 2');

    fireEvent.keyDown(window, { code: 'KeyA' }); // back to library
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it('shows which pane has focus: chapter row ring at the list level, content ring while reading', async () => {
    renderReader();
    await heading('Chapter 1');
    const chapterRow = () => screen.getByTitle('Chapter 1').closest('div')!;

    enterChapterList();
    expect(chapterRow().className).toContain('ring-1');
    expect(document.querySelector('.ring-inset')).toBeNull();

    fireEvent.keyDown(window, { code: 'KeyD' }); // enter the chapter
    expect(chapterRow().className).not.toContain('ring-1');
    expect(document.querySelector('.ring-inset')).not.toBeNull();

    fireEvent.keyDown(window, { code: 'KeyA' }); // back to the chapter list
    expect(chapterRow().className).toContain('ring-1');
    expect(document.querySelector('.ring-inset')).toBeNull();
  });
});

describe('Reader chapter list toggle and font size shortcuts', () => {
  beforeEach(() => {
    localStorage.clear();
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.scrollTo = vi.fn();
  });

  it('hides and re-shows the chapter sidebar with L', async () => {
    const { container } = renderReader();
    await screen.findByText('Chapter 1');
    expect(container.querySelector('[data-reader-nav]')).not.toBeNull();

    fireEvent.keyDown(window, { code: 'KeyL' });
    expect(container.querySelector('[data-reader-nav]')).toBeNull();

    fireEvent.keyDown(window, { code: 'KeyL' });
    expect(container.querySelector('[data-reader-nav]')).not.toBeNull();
  });

  it('grows and shrinks the reading text with =/- and persists the size', async () => {
    const { container } = renderReader();
    await screen.findByRole('heading', { name: 'Chapter 1' });
    const prose = () => container.querySelector<HTMLElement>('.fanfic-prose')!;
    // No per-fic size stored, so it starts at the global content default (16).
    expect(prose().style.fontSize).toBe('16px');

    fireEvent.keyDown(window, { code: 'Equal' });
    expect(prose().style.fontSize).toBe('17px');
    expect(localStorage.getItem('lunaschal:readingFontSize')).toBe('17');

    fireEvent.keyDown(window, { code: 'Minus' });
    fireEvent.keyDown(window, { code: 'Minus' });
    expect(prose().style.fontSize).toBe('15px');
    expect(localStorage.getItem('lunaschal:readingFontSize')).toBe('15');
  });
});

describe('Reader bookmark actions', () => {
  const CONTINUE_BM: FicBookmark = {
    id: 'bm-continue',
    ficId: 'fic1',
    chapterId: 'ch1',
    chapterTitle: 'Chapter 1',
    type: 'continue',
    scrollPosition: 0.5,
    createdAt: '',
  };
  const FAVORITE_BM: FicBookmark = {
    id: 'bm-fav',
    ficId: 'fic1',
    chapterId: 'ch1',
    chapterTitle: 'Chapter 1',
    type: 'favorite',
    scrollPosition: 0.2,
    createdAt: '',
  };

  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.scrollTo = vi.fn();
    vi.mocked(api.fanfic.bookmarks.list).mockResolvedValue([
      CONTINUE_BM,
      FAVORITE_BM,
    ]);
    vi.mocked(api.fanfic.bookmarks.delete).mockReset();
    vi.mocked(api.fanfic.bookmarks.create).mockReset();
  });

  // A tap that lands on a small delete button right next to frequently-used
  // controls (the fic title, a jump-to-bookmark row) must not silently
  // destroy the bookmark — see fix/fanfic-bookmark-mistap.
  it('does not clear the continue bookmark unless the mistap guard is confirmed', async () => {
    vi.mocked(api.fanfic.bookmarks.delete).mockResolvedValue({
      success: true,
    });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderReader();
    const clearButton = await screen.findByTitle('Clear continue bookmark');

    fireEvent.click(clearButton);
    expect(confirmSpy).toHaveBeenCalled();
    expect(api.fanfic.bookmarks.delete).not.toHaveBeenCalled();

    confirmSpy.mockReturnValue(true);
    fireEvent.click(clearButton);
    await waitFor(() =>
      expect(api.fanfic.bookmarks.delete).toHaveBeenCalledWith('bm-continue')
    );

    confirmSpy.mockRestore();
  });

  it('does not remove a favorite unless the mistap guard is confirmed', async () => {
    vi.mocked(api.fanfic.bookmarks.delete).mockResolvedValue({
      success: true,
    });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderReader();

    fireEvent.click(await screen.findByTitle('Favorite bookmarks'));
    const removeButton = await screen.findByTitle('Remove favorite');
    fireEvent.click(removeButton);
    expect(confirmSpy).toHaveBeenCalled();
    expect(api.fanfic.bookmarks.delete).not.toHaveBeenCalled();

    confirmSpy.mockReturnValue(true);
    fireEvent.click(removeButton);
    await waitFor(() =>
      expect(api.fanfic.bookmarks.delete).toHaveBeenCalledWith('bm-fav')
    );

    confirmSpy.mockRestore();
  });

  it('shows an error instead of failing silently when creating a bookmark fails', async () => {
    vi.mocked(api.fanfic.bookmarks.create).mockRejectedValue(
      new Error('HTTP 401')
    );
    renderReader();
    await screen.findByText('Chapter 1');

    fireEvent.click(screen.getByText('Bookmark'));
    fireEvent.click(await screen.findByText('★ Favorite'));

    await screen.findByText('HTTP 401');
  });

  it('shows an error instead of failing silently when clearing a bookmark fails', async () => {
    vi.mocked(api.fanfic.bookmarks.delete).mockRejectedValue(
      new Error('HTTP 500')
    );
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderReader();

    fireEvent.click(await screen.findByTitle('Clear continue bookmark'));
    await screen.findByText('HTTP 500');

    confirmSpy.mockRestore();
  });
});

describe('Reader mobile master-detail bookmark restore', () => {
  // On mobile, useMasterDetail starts on the list pane, so the content div
  // (and its ref) doesn't exist yet when the reader auto-selects the continue
  // bookmark's chapter on mount. The scroll-restore and bookmark-indicator
  // effects used to depend only on data (`chapter`, `bookmarks`, ...), so by
  // the time the user actually opens the chapter and the content div mounts,
  // those effects had already run once with a null ref and never got a
  // second chance — see fix/fanfic-bookmark-mistap.
  const CONTINUE_BM: FicBookmark = {
    id: 'bm-continue',
    ficId: 'fic1',
    chapterId: 'ch2',
    chapterTitle: 'Chapter 2',
    type: 'continue',
    scrollPosition: 0.4,
    createdAt: '',
  };
  let restoreMatchMedia: (() => void) | null = null;

  beforeEach(() => {
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: true,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia;
    restoreMatchMedia = () => {
      window.matchMedia = original;
    };

    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.scrollTo = vi.fn();
    Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
      configurable: true,
      value: 1000,
    });
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
      configurable: true,
      value: 500,
    });
    vi.mocked(api.fanfic.bookmarks.list).mockResolvedValue([CONTINUE_BM]);
  });

  afterEach(() => {
    restoreMatchMedia?.();
    restoreMatchMedia = null;
  });

  it('restores the continue bookmark scroll position once the content pane actually mounts', async () => {
    renderReader();

    // Mobile opens on the chapter list; the auto-select effect has already
    // picked Chapter 2 (the continue bookmark's chapter) in the background.
    const chapter2Row = await screen.findByTitle('Chapter 2');
    fireEvent.click(chapter2Row);

    await waitFor(() => {
      expect(Element.prototype.scrollTo).toHaveBeenCalledWith({ top: 200 });
    });
  });

  it('shows the continue-bookmark indicator line once the content pane mounts', async () => {
    const { container } = renderReader();

    const chapter2Row = await screen.findByTitle('Chapter 2');
    fireEvent.click(chapter2Row);

    await waitFor(() => {
      const indicator = container.querySelector(
        '[style*="background-color: rgb(34, 197, 94)"]'
      );
      expect(indicator).not.toBeNull();
    });
  });
});

describe('Reader commentary microphone', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.scrollTo = vi.fn();
  });

  // Dictated commentary saves itself. You are mid-chapter when you say it, not
  // looking at the box — and every dictation now goes through the two-model STT
  // cross-check, which is what the read-it-back-then-click step was for.
  it('records and saves the transcript straight to the journal', async () => {
    class FakeMediaRecorder {
      onstop: (() => void) | null = null;
      ondataavailable: ((e: { data: Blob }) => void) | null = null;
      start() {}
      stop() {
        this.onstop?.();
      }
    }
    vi.stubGlobal('MediaRecorder', FakeMediaRecorder);
    vi.stubGlobal('navigator', {
      ...navigator,
      mediaDevices: {
        getUserMedia: vi
          .fn()
          .mockResolvedValue({ getTracks: () => [], getAudioTracks: () => [] }),
      },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ text: 'loved this chapter' }),
      })
    );

    renderReader();
    fireEvent.click(await screen.findByText(/Commentary/));
    fireEvent.click(screen.getByRole('button', { name: '🎤' }));

    const stop = await screen.findByRole('button', { name: '■ Stop' });
    fireEvent.click(stop);

    await waitFor(() =>
      expect(api.journal.createFromVoice).toHaveBeenCalledWith(
        'loved this chapter'
      )
    );
    // Linked to the fic and the open chapter, the same as a typed-then-saved
    // one. (Which chapter is open depends on where the reader last was, so it
    // is deliberately not pinned here.)
    await waitFor(() =>
      expect(api.fanfic.linkJournal).toHaveBeenCalledWith(
        'fic1',
        'j1',
        expect.any(String)
      )
    );
    // …and the box is emptied, not left holding a copy of what was saved.
    await waitFor(() =>
      expect(
        (screen.getByPlaceholderText(/Your thoughts on/) as HTMLTextAreaElement)
          .value
      ).toBe('')
    );
    vi.unstubAllGlobals();
  });

  it('saves what was typed together with what was spoken', async () => {
    class FakeMediaRecorder {
      onstop: (() => void) | null = null;
      ondataavailable: ((e: { data: Blob }) => void) | null = null;
      start() {}
      stop() {
        this.onstop?.();
      }
    }
    vi.stubGlobal('MediaRecorder', FakeMediaRecorder);
    vi.stubGlobal('navigator', {
      ...navigator,
      mediaDevices: {
        getUserMedia: vi
          .fn()
          .mockResolvedValue({ getTracks: () => [], getAudioTracks: () => [] }),
      },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ text: 'loved this chapter' }),
      })
    );

    renderReader();
    fireEvent.click(await screen.findByText(/Commentary/));
    fireEvent.change(screen.getByPlaceholderText(/Your thoughts on/), {
      target: { value: 'ch3 spoiler:' },
    });
    fireEvent.click(screen.getByRole('button', { name: '🎤' }));
    fireEvent.click(await screen.findByRole('button', { name: '■ Stop' }));

    await waitFor(() =>
      expect(api.journal.createFromVoice).toHaveBeenCalledWith(
        'ch3 spoiler:\nloved this chapter'
      )
    );
    vi.unstubAllGlobals();
  });
});
