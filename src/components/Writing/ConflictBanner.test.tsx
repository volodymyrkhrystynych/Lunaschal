// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { ShortcutProvider } from '../../shortcuts/ShortcutProvider';
import { api, ApiError } from '../../hooks/api';
import { registerOfflineMutationDefaults } from '../../offline/mutationDefaults';
import { documentKey, heldConflict } from '../../lib/writingDocuments';
import { ChapterEditor } from './ChapterEditor';
import { NoteEditor } from './NoteEditor';

vi.mock('../../hooks/api', () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  }
  class NetworkError extends Error {}
  return {
    ApiError,
    NetworkError,
    api: {
      shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
      settings: { get: vi.fn().mockResolvedValue({}) },
      writing: {
        getChapter: vi.fn(),
        updateChapter: vi.fn(),
        getNote: vi.fn(),
        updateNote: vi.fn(),
      },
    },
  };
});

function renderEditor(ui: ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  registerOfflineMutationDefaults(qc);
  return render(
    <QueryClientProvider client={qc}>
      <ShortcutProvider>{ui}</ShortcutProvider>
    </QueryClientProvider>
  );
}

const chapter = (id: string, content: string, revision: number) => ({
  id,
  projectId: 'p',
  title: 'Chapter',
  content,
  position: 0,
  revision,
  createdAt: '',
  updatedAt: '',
});

async function typeAndWaitForSave(placeholder: string, text: string) {
  const box = await screen.findByPlaceholderText(placeholder);
  await waitFor(() =>
    expect((box as HTMLTextAreaElement).value).not.toBe('loading')
  );
  fireEvent.change(box, { target: { value: text } });
  return box as HTMLTextAreaElement;
}

beforeEach(() => {
  localStorage.clear();
  vi.mocked(api.writing.updateChapter).mockReset();
  vi.mocked(api.writing.getChapter).mockReset();
  vi.mocked(api.writing.updateNote).mockReset();
  vi.mocked(api.writing.getNote).mockReset();
});

describe('saving a chapter another device changed', () => {
  it('sends the revision it was edited from', async () => {
    vi.mocked(api.writing.getChapter).mockResolvedValue(
      chapter('sends', 'Old.', 4)
    );
    vi.mocked(api.writing.updateChapter).mockResolvedValue({
      success: true,
      revision: 5,
    });
    renderEditor(<ChapterEditor chapterId="sends" />);
    await screen.findByDisplayValue('Old.');
    await typeAndWaitForSave('Start writing…', 'New.');
    await waitFor(
      () =>
        expect(api.writing.updateChapter).toHaveBeenCalledWith('sends', {
          title: undefined,
          content: 'New.',
          baseRevision: 4,
        }),
      { timeout: 3000 }
    );
  });

  it('keeps the refused text, shows the banner, and stops saving', async () => {
    vi.mocked(api.writing.getChapter).mockResolvedValue(
      chapter('refused', 'Old.', 1)
    );
    vi.mocked(api.writing.updateChapter).mockRejectedValue(
      new ApiError('changed on another device', 409)
    );
    renderEditor(<ChapterEditor chapterId="refused" />);
    await screen.findByDisplayValue('Old.');
    const box = await typeAndWaitForSave('Start writing…', 'Mine.');
    expect(
      (await screen.findByRole('alert', {}, { timeout: 3000 })).textContent
    ).toContain('another device');
    expect(heldConflict(documentKey('chapter', 'refused'))).toBe('Mine.');
    expect(box.value).toBe('Mine.');

    fireEvent.change(box, { target: { value: 'Mine, and more.' } });
    expect(heldConflict(documentKey('chapter', 'refused'))).toBe(
      'Mine, and more.'
    );
    await new Promise(resolve => setTimeout(resolve, 1700));
    expect(api.writing.updateChapter).toHaveBeenCalledTimes(1);
  }, 8000);

  it('offers the refused text again after a reload', async () => {
    localStorage.setItem(
      'lunaschal:writingConflict:chapter:reloaded',
      'Survived.'
    );
    vi.mocked(api.writing.getChapter).mockResolvedValue(
      chapter('reloaded', 'Theirs.', 3)
    );
    renderEditor(<ChapterEditor chapterId="reloaded" />);
    expect(await screen.findByDisplayValue('Survived.')).not.toBeNull();
    expect(await screen.findByRole('alert')).not.toBeNull();
  });

  it('keeps mine by saving over the latest revision', async () => {
    localStorage.setItem('lunaschal:writingConflict:chapter:keep', 'Mine.');
    vi.mocked(api.writing.getChapter).mockResolvedValue(
      chapter('keep', 'Theirs.', 8)
    );
    vi.mocked(api.writing.updateChapter).mockResolvedValue({
      success: true,
      revision: 9,
    });
    renderEditor(<ChapterEditor chapterId="keep" />);
    fireEvent.click(await screen.findByText('Keep mine'));
    await waitFor(() =>
      expect(api.writing.updateChapter).toHaveBeenCalledWith('keep', {
        title: undefined,
        content: 'Mine.',
        baseRevision: 8,
      })
    );
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
    expect(heldConflict(documentKey('chapter', 'keep'))).toBe(null);
  });

  it('loads the latest text and lets mine go', async () => {
    localStorage.setItem('lunaschal:writingConflict:chapter:load', 'Mine.');
    vi.mocked(api.writing.getChapter).mockResolvedValue(
      chapter('load', 'Theirs.', 8)
    );
    renderEditor(<ChapterEditor chapterId="load" />);
    fireEvent.click(await screen.findByText('Load latest'));
    expect(await screen.findByDisplayValue('Theirs.')).not.toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(heldConflict(documentKey('chapter', 'load'))).toBe(null);
  });
});

describe('saving a note another device changed', () => {
  it('shows the same banner', async () => {
    vi.mocked(api.writing.getNote).mockResolvedValue({
      id: 'n',
      projectId: 'p',
      title: 'Note',
      content: 'Old.',
      docType: 'note',
      revision: 1,
      createdAt: '',
      updatedAt: '',
    });
    vi.mocked(api.writing.updateNote).mockRejectedValue(
      new ApiError('changed on another device', 409)
    );
    renderEditor(<NoteEditor noteId="n" />);
    await screen.findByDisplayValue('Old.');
    await typeAndWaitForSave('Write your note…', 'Mine.');
    expect(
      await screen.findByRole('alert', {}, { timeout: 3000 })
    ).not.toBeNull();
    expect(heldConflict(documentKey('note', 'n'))).toBe('Mine.');
  });
});
