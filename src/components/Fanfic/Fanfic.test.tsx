// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Fanfic } from './Fanfic';
import { ShortcutProvider } from '../../shortcuts/ShortcutProvider';
import { setStoredFicTarget } from '../../lib/fanficPersistence';
import type { Fic, FicChapterSummary } from '../../hooks/api';

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
  ];
  const FIC: Fic = {
    id: 'fic1',
    title: 'Test Fic',
    author: 'Author',
    sourceType: 'xenforo',
    sourceUrl: null,
    site: null,
    description: 'A test summary.',
    coverPath: null,
    wordCount: 100,
    chapterCount: 1,
    downloadStatus: 'complete',
    downloadError: null,
    lastReadChapterId: null,
    lastCheckedAt: null,
    rating: null,
    review: 'A test review.',
    createdAt: '2024-01-02T03:04:05Z',
    updatedAt: '2024-01-02T03:04:05Z',
  };
  return { CHAPTERS, FIC };
});

vi.mock('../../hooks/api', () => ({
  api: {
    fanfic: {
      get: vi.fn().mockResolvedValue(FIC),
      markOpened: vi.fn().mockResolvedValue({ success: true }),
      list: vi.fn().mockResolvedValue([FIC]),
      folders: vi.fn().mockResolvedValue([]),
      chapters: vi.fn().mockResolvedValue(CHAPTERS),
      chapter: vi.fn().mockResolvedValue({
        ...CHAPTERS[0],
        contentHtml: '<p>text</p>',
        contentText: 'text',
        sourceUrl: null,
        createdAt: '',
      }),
      saveProgress: vi.fn().mockResolvedValue({ success: true }),
      setRead: vi.fn().mockResolvedValue({ success: true, readCount: 0 }),
      bookmarks: {
        list: vi.fn().mockResolvedValue([]),
        create: vi.fn(),
        delete: vi.fn(),
      },
      checkUpdates: vi
        .fn()
        .mockResolvedValue({ id: 'fic1', queued: true, deep: false }),
      uploadFile: vi.fn().mockResolvedValue({ id: 'fic2' }),
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

function renderFanfic() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="fanfic" onViewChange={() => {}}>
        <Fanfic />
      </ShortcutProvider>
    </QueryClientProvider>
  );
}

describe('Fanfic reload resume', () => {
  beforeEach(() => {
    localStorage.clear();
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.scrollTo = vi.fn();
  });

  it('opens straight into the Reader when a fic was left open before reload', async () => {
    setStoredFicTarget({ ficId: 'fic1' });
    renderFanfic();
    await screen.findByRole('heading', { name: 'Chapter 1' });
  });

  it('clears the stored target when backing out to the library', async () => {
    setStoredFicTarget({ ficId: 'fic1' });
    renderFanfic();
    await screen.findByRole('heading', { name: 'Chapter 1' });

    screen.getAllByText('← Library')[0].click();

    await screen.findByText('Test Fic');
    expect(localStorage.getItem('lunaschal:openFic')).toBeNull();
  });
});

describe('Library infinite scroll', () => {
  beforeEach(() => {
    localStorage.clear();
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.scrollTo = vi.fn();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('fetches the next page once the sentinel intersects', async () => {
    const { api } = await import('../../hooks/api');
    const page1 = Array.from({ length: 50 }, (_, i) => ({
      ...FIC,
      id: `fic${i}`,
      title: `Fic ${i}`,
    }));
    const page2 = [{ ...FIC, id: 'fic50', title: 'Fic 50' }];
    vi.mocked(api.fanfic.list)
      .mockResolvedValueOnce(page1)
      .mockResolvedValueOnce(page2);

    let intersect: IntersectionObserverCallback = () => {};
    class FakeIntersectionObserver {
      constructor(cb: IntersectionObserverCallback) {
        intersect = cb;
      }
      observe() {}
      disconnect() {}
      unobserve() {}
    }
    vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver);

    renderFanfic();
    await screen.findByText('Fic 0');

    intersect(
      [{ isIntersecting: true } as IntersectionObserverEntry],
      {} as IntersectionObserver
    );

    await screen.findByText('Fic 50');
    expect(api.fanfic.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ limit: 50, offset: 50 })
    );
  });
});

describe('Library views and expandable details', () => {
  beforeEach(() => {
    localStorage.clear();
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.scrollTo = vi.fn();
  });

  it('keeps summary and review collapsed until Details is expanded', async () => {
    renderFanfic();
    expect(screen.queryByText('A test summary.')).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: /Details/ }));
    expect(screen.getByText('A test summary.')).toBeTruthy();
    expect(screen.getByText('A test review.')).toBeTruthy();
  });

  it('expands from the card surface while the title opens the reader', async () => {
    renderFanfic();
    const title = await screen.findByRole('button', { name: 'Test Fic' });
    const card = title.closest('.rounded-lg.border') as HTMLElement;

    fireEvent.click(card);
    expect(screen.getByText('A test summary.')).toBeTruthy();

    fireEvent.click(title);
    await screen.findByRole('heading', { name: 'Chapter 1' });
  });

  it('shows the personal rating in expanded details', async () => {
    const { api } = await import('../../hooks/api');
    vi.mocked(api.fanfic.list).mockResolvedValueOnce([{ ...FIC, rating: 4 }]);
    renderFanfic();

    fireEvent.click(await screen.findByRole('button', { name: /Details/ }));
    expect(screen.getByText('My rating')).toBeTruthy();
    expect(screen.getAllByText('★★★★☆').length).toBeGreaterThan(0);
  });

  it('offers Library and Folders as separate views', async () => {
    renderFanfic();
    const folders = await screen.findByRole('tab', { name: 'Folders' });
    fireEvent.click(folders);
    expect(folders.getAttribute('aria-selected')).toBe('true');
    expect(screen.getByText(/Choose a folder/)).toBeTruthy();
  });

  it('offers forum and file as the two sources under one Import button', async () => {
    const { api } = await import('../../hooks/api');
    const { container } = renderFanfic();

    fireEvent.click(await screen.findByRole('button', { name: '+ Import' }));
    expect(
      screen.getByPlaceholderText(/forums\.spacebattles\.com/)
    ).toBeTruthy();

    fireEvent.click(screen.getByRole('tab', { name: 'Upload file' }));
    expect(screen.queryByPlaceholderText(/forums\.spacebattles\.com/)).toBe(
      null
    );

    const file = new File(['x'], 'fic.epub');
    const input = container.querySelector(
      'input[type="file"]'
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() =>
      expect(api.fanfic.uploadFile).toHaveBeenCalledWith(file)
    );
    // The panel closes on a successful upload, like a forum import does.
    await waitFor(() =>
      expect(screen.queryByRole('tab', { name: 'Upload file' })).toBe(null)
    );
  });
});

describe('Library update buttons', () => {
  beforeEach(() => {
    localStorage.clear();
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.scrollTo = vi.fn();
  });

  it('queues a shallow check from Update and a deep one from Deep', async () => {
    const { api } = await import('../../hooks/api');
    renderFanfic();

    fireEvent.click(await screen.findByTitle(/Queue an update check/));
    await waitFor(() =>
      expect(api.fanfic.checkUpdates).toHaveBeenLastCalledWith('fic1', false)
    );

    fireEvent.click(screen.getByTitle(/Re-read every saved chapter/));
    await waitFor(() =>
      expect(api.fanfic.checkUpdates).toHaveBeenLastCalledWith('fic1', true)
    );
  });
});
