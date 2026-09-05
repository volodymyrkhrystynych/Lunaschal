// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { api } from '../../hooks/api';
import type { StudySource } from '../../lib/study';
import { Study } from './Study';

// pdf.js reaches for a Worker and a canvas 2d context, neither of which jsdom
// has. The viewer's own rendering isn't what these tests are about.
vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: { workerSrc: '' },
  getDocument: () => ({
    promise: Promise.resolve({ numPages: 0, destroy: () => Promise.resolve() }),
    destroy: () => Promise.resolve(),
  }),
}));

function source(overrides: Partial<StudySource> = {}): StudySource {
  return {
    id: 's1',
    title: 'Attention Is All You Need',
    kind: 'pdf',
    sourceUrl: null,
    contentType: 'application/pdf',
    sizeBytes: 2_400_000,
    durationSeconds: null,
    notePath: 'study/attention.md',
    importStatus: 'ready',
    importError: null,
    lastOpenedAt: null,
    createdAt: '2026-09-04T10:00:00+00:00',
    updatedAt: '2026-09-04T10:00:00+00:00',
    ...overrides,
  };
}

function renderStudy() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return render(<Study />, { wrapper: Wrapper });
}

beforeEach(() => {
  vi.spyOn(api.study, 'update').mockResolvedValue(source());
  // The note pane mounts the real Notebook editor, which reads its file.
  vi.spyOn(api.notebook.files, 'read').mockResolvedValue({ content: '' });
  vi.spyOn(api.notebook.files, 'ensure').mockResolvedValue(undefined);
  vi.spyOn(api.notebook.review, 'getState').mockResolvedValue({
    path: 'study/attention.md',
    enabled: false,
  } as never);
});

afterEach(() => vi.restoreAllMocks());

describe('the Study library', () => {
  it('lists sources with what each one is', async () => {
    vi.spyOn(api.study, 'sources').mockResolvedValue([
      source(),
      source({
        id: 's2',
        kind: 'youtube',
        title: 'Lecture 1: Backprop',
        durationSeconds: 3771,
        sizeBytes: 0,
        notePath: null,
      }),
    ]);
    renderStudy();

    await waitFor(() =>
      expect(screen.getByText('Attention Is All You Need')).toBeTruthy()
    );
    expect(screen.getByText(/PDF · 2\.3 MB/)).toBeTruthy();
    expect(screen.getByText(/Video · 1:02:51/)).toBeTruthy();
  });

  it('shows an import error instead of opening a source that has none', async () => {
    vi.spyOn(api.study, 'sources').mockResolvedValue([
      source({
        importStatus: 'error',
        importError: 'ERROR: Video unavailable',
        kind: 'youtube',
        sourceUrl: 'https://youtu.be/abc',
      }),
    ]);
    renderStudy();

    await waitFor(() =>
      expect(screen.getByText('ERROR: Video unavailable')).toBeTruthy()
    );
    const open = screen
      .getByText('Attention Is All You Need')
      .closest('button') as HTMLButtonElement;
    expect(open.disabled).toBe(true);
    expect(screen.getByText('Retry')).toBeTruthy();
  });

  it('imports a website through the web endpoint', async () => {
    vi.spyOn(api.study, 'sources').mockResolvedValue([]);
    const importWeb = vi
      .spyOn(api.study, 'importWeb')
      .mockResolvedValue({ id: 's9', source: source({ kind: 'web' }) });
    renderStudy();

    await waitFor(() =>
      expect(screen.getByText(/Nothing here yet/)).toBeTruthy()
    );
    fireEvent.click(screen.getByText('🌐 Import website'));
    const field = screen.getByLabelText('Website URL');
    fireEvent.change(field, { target: { value: 'https://example.com/a' } });
    fireEvent.keyDown(field, { key: 'Enter' });

    await waitFor(() =>
      expect(importWeb).toHaveBeenCalledWith('https://example.com/a')
    );
  });
});

describe('the Study desk', () => {
  it('opens a source beside its note', async () => {
    vi.spyOn(api.study, 'sources').mockResolvedValue([source()]);
    vi.spyOn(api.study, 'source').mockResolvedValue(source());
    renderStudy();

    await waitFor(() =>
      expect(screen.getByText('Attention Is All You Need')).toBeTruthy()
    );
    fireEvent.click(screen.getByText('Attention Is All You Need'));

    // Left half: the PDF viewer's own toolbar.
    await waitFor(() =>
      expect(screen.getByLabelText('Next page')).toBeTruthy()
    );
    // Right half: the notebook editor, on the bound note.
    expect(screen.getAllByText('study/attention.md').length).toBeGreaterThan(0);
    // And the way back.
    expect(screen.getByText('‹ Sources')).toBeTruthy();
  });

  it('creates and binds a note for a source that has none', async () => {
    const unbound = source({ notePath: null });
    vi.spyOn(api.study, 'sources').mockResolvedValue([unbound]);
    vi.spyOn(api.study, 'source').mockResolvedValue(unbound);
    renderStudy();

    await waitFor(() =>
      expect(screen.getByText('Attention Is All You Need')).toBeTruthy()
    );
    fireEvent.click(screen.getByText('Attention Is All You Need'));

    await waitFor(() =>
      expect(api.notebook.files.ensure).toHaveBeenCalledWith(
        'study/attention-is-all-you-need-s1.md'
      )
    );
    expect(api.study.update).toHaveBeenCalledWith('s1', {
      notePath: 'study/attention-is-all-you-need-s1.md',
    });
  });
});
