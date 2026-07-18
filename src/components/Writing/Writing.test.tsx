// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ShortcutProvider } from '../../shortcuts/ShortcutProvider';
import { CHAPTER_FONT_SIZE_MAX } from '../../lib/fontSize';
import { api } from '../../hooks/api';
import { Writing } from './index';
import { ChapterEditor } from './ChapterEditor';

vi.mock('../../hooks/api', () => ({
  api: {
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
    writing: {
      listProjects: vi.fn().mockResolvedValue([]),
      getProject: vi
        .fn()
        .mockResolvedValue({ id: 'p1', title: 'My Story', description: null }),
      createProject: vi.fn(),
      deleteProject: vi.fn(),
      listChapters: vi.fn().mockResolvedValue([]),
      getChapter: vi.fn().mockResolvedValue({
        id: 'ch1',
        title: 'Chapter One',
        content: 'hello world',
      }),
      updateChapter: vi.fn().mockResolvedValue({}),
      createChapter: vi.fn(),
      deleteChapter: vi.fn(),
      listNotes: vi.fn().mockResolvedValue([]),
      getNote: vi.fn().mockResolvedValue({
        id: 'n1',
        projectId: 'p1',
        title: 'Villain ideas',
        content: 'so evil',
        docType: 'note',
      }),
      createNote: vi.fn(),
      updateNote: vi.fn().mockResolvedValue({}),
      deleteNote: vi.fn(),
      listDiscussions: vi.fn().mockResolvedValue([]),
      createDiscussion: vi.fn(),
      summarizeDiscussion: vi.fn(),
    },
    chat: {
      getConversation: vi
        .fn()
        .mockResolvedValue({ id: 'd1', title: 'Plot talk', messages: [] }),
      updateTitle: vi.fn(),
      deleteConversation: vi.fn(),
      addMessage: vi.fn(),
    },
  },
}));

beforeEach(() => {
  localStorage.clear();
});

function renderWithProviders(children: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="writing" onViewChange={() => {}}>
        {children}
      </ShortcutProvider>
    </QueryClientProvider>
  );
}

function mockProjectWithItems() {
  vi.mocked(api.writing.listProjects).mockResolvedValue([
    {
      id: 'p1',
      title: 'My Story',
      description: null,
      createdAt: '',
      updatedAt: '',
    },
  ]);
  vi.mocked(api.writing.listChapters).mockResolvedValue([
    {
      id: 'ch1',
      projectId: 'p1',
      title: 'Chapter One',
      position: 0,
      createdAt: '',
      updatedAt: '',
    },
  ]);
  vi.mocked(api.writing.listNotes).mockResolvedValue([
    {
      id: 'n1',
      projectId: 'p1',
      title: 'Villain ideas',
      docType: 'note',
      createdAt: '',
      updatedAt: '',
    },
  ]);
  vi.mocked(api.writing.listDiscussions).mockResolvedValue([
    { id: 'd1', title: 'Plot talk', createdAt: '', updatedAt: '' },
  ]);
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
    localStorage.setItem(
      'lunaschal:chapterFontSize',
      String(CHAPTER_FONT_SIZE_MAX)
    );
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

describe('writing nav sections', () => {
  it('lists chapters, notes, and discussions after selecting a project', async () => {
    mockProjectWithItems();
    renderWithProviders(<Writing />);

    fireEvent.click(await screen.findByText('My Story'));

    expect(await screen.findByText('Chapters')).not.toBeNull();
    expect(screen.getByText('Notes')).not.toBeNull();
    expect(screen.getByText('Discussions')).not.toBeNull();
    expect(await screen.findByText('Chapter One')).not.toBeNull();
    expect(await screen.findByText('Villain ideas')).not.toBeNull();
    expect(await screen.findByText('Plot talk')).not.toBeNull();
  });

  it('opens the selected item in the center panel', async () => {
    mockProjectWithItems();
    renderWithProviders(<Writing />);
    fireEvent.click(await screen.findByText('My Story'));

    fireEvent.click(await screen.findByText('Chapter One'));
    expect(await screen.findByPlaceholderText('Start writing…')).not.toBeNull();

    fireEvent.click(screen.getByText('Villain ideas'));
    expect(
      await screen.findByPlaceholderText('Write your note…')
    ).not.toBeNull();
    expect(screen.queryByPlaceholderText('Start writing…')).toBeNull();

    fireEvent.click(screen.getByText('Plot talk'));
    expect(
      await screen.findByPlaceholderText('Discuss your story… (Enter to send)')
    ).not.toBeNull();
    expect(screen.queryByPlaceholderText('Write your note…')).toBeNull();
  });

  it('steps across section boundaries with keyboard nav', async () => {
    mockProjectWithItems();
    renderWithProviders(<Writing />);
    fireEvent.click(await screen.findByText('My Story'));
    await screen.findByText('Chapter One');

    // Drill from the view level into the project list, then into the nav
    fireEvent.keyDown(window, { code: 'KeyD' });
    fireEvent.keyDown(window, { code: 'KeyD' });

    // First step selects the first chapter
    fireEvent.keyDown(window, { code: 'KeyS' });
    expect(await screen.findByPlaceholderText('Start writing…')).not.toBeNull();

    // Next step crosses from the last chapter into the first note
    fireEvent.keyDown(window, { code: 'KeyS' });
    expect(
      await screen.findByPlaceholderText('Write your note…')
    ).not.toBeNull();

    // And from the last note into the first discussion
    fireEvent.keyDown(window, { code: 'KeyS' });
    expect(
      await screen.findByPlaceholderText('Discuss your story… (Enter to send)')
    ).not.toBeNull();
  });
});
