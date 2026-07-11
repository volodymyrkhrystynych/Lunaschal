// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Reader } from './Reader';
import { ShortcutProvider } from '../../shortcuts/ShortcutProvider';
import type { Fic, FicChapter, FicChapterSummary } from '../../hooks/api';

const { CHAPTERS, FIC } = vi.hoisted(() => {
  const CHAPTERS: FicChapterSummary[] = [
    { id: 'ch1', ficId: 'fic1', position: 1, title: 'Chapter 1', category: 'Chapters', wordCount: 100, postedAt: null, isRead: false },
    { id: 'ch2', ficId: 'fic1', position: 2, title: 'Chapter 2', category: 'Chapters', wordCount: 100, postedAt: null, isRead: false },
    { id: 'ch3', ficId: 'fic1', position: 3, title: 'Chapter 3', category: 'Chapters', wordCount: 100, postedAt: null, isRead: false },
  ];
  const FIC: Fic = {
    id: 'fic1', title: 'Test Fic', author: 'Author', sourceType: 'xenforo', sourceUrl: null, site: null,
    description: null, coverPath: null, wordCount: 300, chapterCount: 3, downloadStatus: 'complete',
    downloadError: null, lastReadChapterId: null, lastCheckedAt: null, rating: null,
    createdAt: '', updatedAt: '',
  };
  return { CHAPTERS, FIC };
});

vi.mock('../../hooks/api', () => ({
  api: {
    fanfic: {
      get: vi.fn().mockResolvedValue(FIC),
      chapters: vi.fn().mockResolvedValue(CHAPTERS),
      chapter: vi.fn().mockImplementation((id: string) => {
        const summary = CHAPTERS.find((c) => c.id === id)!;
        const chapter: FicChapter = { ...summary, contentHtml: '<p>text</p>', contentText: 'text', sourceUrl: null, createdAt: '' };
        return Promise.resolve(chapter);
      }),
      saveProgress: vi.fn().mockResolvedValue({ success: true }),
      setRead: vi.fn().mockResolvedValue({ success: true, readCount: 0 }),
    },
    shortcuts: {
      get: vi.fn().mockResolvedValue({ bindings: {} }),
    },
  },
}));

function renderReader() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="fanfic" onViewChange={() => {}}>
        <Reader ficId="fic1" onBack={() => {}} />
      </ShortcutProvider>
    </QueryClientProvider>,
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

    const scrollSpy = Element.prototype.scrollIntoView as unknown as ReturnType<typeof vi.fn>;
    const callsBefore = scrollSpy.mock.calls.length;

    fireEvent.click(screen.getByText('Chapter 3'));

    await waitFor(() => {
      expect(scrollSpy.mock.calls.length).toBeGreaterThan(callsBefore);
    });
    expect(scrollSpy.mock.calls.at(-1)?.[0]).toEqual({ block: 'nearest' });
  });
});
