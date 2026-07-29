// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
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
    description: null,
    coverPath: null,
    wordCount: 100,
    chapterCount: 1,
    downloadStatus: 'complete',
    downloadError: null,
    lastReadChapterId: null,
    lastCheckedAt: null,
    rating: null,
    createdAt: '',
    updatedAt: '',
  };
  return { CHAPTERS, FIC };
});

vi.mock('../../hooks/api', () => ({
  api: {
    fanfic: {
      get: vi.fn().mockResolvedValue(FIC),
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
