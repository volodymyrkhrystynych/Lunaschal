// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Lessons } from './Lessons';

const { mocks } = vi.hoisted(() => ({
  mocks: {
    listLessons: vi.fn(),
    listFolders: vi.fn(),
    importLessons: vi.fn(),
    scanLessons: vi.fn(),
    updateLessonsConfig: vi.fn(),
    forceLesson: vi.fn(),
  },
}));

vi.mock('../../hooks/api', () => ({ api: { learning: mocks } }));

function renderLessons() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Lessons />
    </QueryClientProvider>
  );
}

const covered = {
  id: 'L1',
  source: 'inbox',
  sourceName: '2026-10-11.md',
  title: 'Retries need a cap',
  body: '',
  folderId: null,
  status: 'covered',
  coveredBy: 'c1',
  coveredQuestion: 'What limits does a retry loop need?',
  reason: 'Same principle.',
  cardCount: 0,
  error: null,
  createdAt: '',
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listLessons.mockResolvedValue({
    lessons: [covered],
    config: { inboxPath: '~/learnings', folderId: null, inboxState: 'ok' },
  });
  mocks.listFolders.mockResolvedValue([
    { id: 'f1', name: 'Engineering', system: false },
    { id: 'r', name: 'For review', system: true },
  ]);
  mocks.importLessons.mockResolvedValue({ added: 2, alreadyImported: 1 });
  mocks.forceLesson.mockResolvedValue({ success: true });
  mocks.updateLessonsConfig.mockResolvedValue({});
});

describe('Lessons', () => {
  it('shows why a lesson was skipped and can card it anyway', async () => {
    renderLessons();
    expect(await screen.findByText('Already covered')).toBeTruthy();
    expect(
      screen.getByText('What limits does a retry loop need?')
    ).toBeTruthy();
    expect(screen.getByText('Same principle.')).toBeTruthy();
    fireEvent.click(screen.getByText('Make cards anyway'));
    await waitFor(() => expect(mocks.forceLesson.mock.calls[0][0]).toBe('L1'));
  });

  it('imports pasted text and says what happened', async () => {
    renderLessons();
    await screen.findByText('Already covered');
    fireEvent.change(screen.getByPlaceholderText(/Paste a learnings file/), {
      target: { value: '## Bound recursion\nCycles.' },
    });
    fireEvent.click(screen.getByText('Import'));
    expect(
      await screen.findByText('2 new lessons · 1 already imported')
    ).toBeTruthy();
    expect(mocks.importLessons).toHaveBeenCalledWith({
      text: '## Bound recursion\nCycles.',
      sourceName: undefined,
    });
  });

  it('offers only user folders as the destination', async () => {
    renderLessons();
    const select = (await screen.findByLabelText(
      'Destination folder'
    )) as HTMLSelectElement;
    await screen.findByText('Engineering');
    expect([...select.options].map(o => o.text)).toEqual([
      'No folder',
      'Engineering',
    ]);
    fireEvent.change(select, { target: { value: 'f1' } });
    await waitFor(() =>
      expect(mocks.updateLessonsConfig.mock.calls[0][0]).toEqual({
        folderId: 'f1',
      })
    );
  });
});
