// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ShortcutProvider } from '../../shortcuts/ShortcutProvider';
import { CHAPTER_FONT_SIZE_MAX } from '../../lib/fontSize';
import { Writing } from './index';
import { ChapterEditor } from './ChapterEditor';

vi.mock('../../hooks/api', () => ({
  api: {
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    writing: {
      listProjects: vi.fn().mockResolvedValue([]),
      getProject: vi.fn(),
      listChapters: vi.fn().mockResolvedValue([]),
      getChapter: vi.fn().mockResolvedValue({ id: 'ch1', title: 'Chapter One', content: 'hello world' }),
      updateChapter: vi.fn().mockResolvedValue({}),
      createProject: vi.fn(),
      deleteProject: vi.fn(),
      createChapter: vi.fn(),
      deleteChapter: vi.fn(),
    },
  },
}));

beforeEach(() => {
  localStorage.clear();
});

function renderWithProviders(children: ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="writing" onViewChange={() => {}}>
        {children}
      </ShortcutProvider>
    </QueryClientProvider>,
  );
}

describe('chapter font size shortcuts', () => {
  it('grows and shrinks the chapter text with =/- and persists the size', async () => {
    renderWithProviders(<ChapterEditor chapterId="ch1" />);
    const textarea = await screen.findByPlaceholderText('Start writing…');
    expect(textarea.style.fontSize).toBe('16px');

    fireEvent.keyDown(window, { code: 'Equal' });
    expect(textarea.style.fontSize).toBe('17px');
    expect(localStorage.getItem('lunaschal:chapterFontSize')).toBe('17');

    fireEvent.keyDown(window, { code: 'Minus' });
    fireEvent.keyDown(window, { code: 'Minus' });
    expect(textarea.style.fontSize).toBe('15px');
    expect(localStorage.getItem('lunaschal:chapterFontSize')).toBe('15');
  });

  it('starts from the stored size and clamps at the maximum', async () => {
    localStorage.setItem('lunaschal:chapterFontSize', String(CHAPTER_FONT_SIZE_MAX));
    renderWithProviders(<ChapterEditor chapterId="ch1" />);
    const textarea = await screen.findByPlaceholderText('Start writing…');
    expect(textarea.style.fontSize).toBe(`${CHAPTER_FONT_SIZE_MAX}px`);

    fireEvent.keyDown(window, { code: 'Equal' });
    expect(textarea.style.fontSize).toBe(`${CHAPTER_FONT_SIZE_MAX}px`);
  });

  it('does not resize while typing in the chapter textarea', async () => {
    renderWithProviders(<ChapterEditor chapterId="ch1" />);
    const textarea = await screen.findByPlaceholderText('Start writing…');
    textarea.focus();

    fireEvent.keyDown(textarea, { code: 'Equal' });
    expect(textarea.style.fontSize).toBe('16px');
  });
});

describe('chapter list toggle shortcut', () => {
  it('hides and re-shows the project/chapter nav with L', async () => {
    const { container } = renderWithProviders(<Writing />);
    expect(container.querySelector('[data-writing-nav]')).not.toBeNull();

    fireEvent.keyDown(window, { code: 'KeyL' });
    expect(container.querySelector('[data-writing-nav]')).toBeNull();

    fireEvent.keyDown(window, { code: 'KeyL' });
    expect(container.querySelector('[data-writing-nav]')).not.toBeNull();
  });
});
