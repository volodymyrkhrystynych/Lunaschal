// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useImperativeHandle, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { api } from '../../hooks/api';
import type { StudySource } from '../../lib/study';
import type { PaperEditorHandle } from '../Paper/PaperEditor';
import { StudyDesk } from './StudyDesk';

// The real editor drags in a canvas, IndexedDB and the offline write lane, and
// has 1,300 lines of its own tests. What is under test here is the *desk's*
// side of the contract: which pane it mounts, what it tells the server, and
// that it commits the open page before it stops rendering one.
const commitLocal = vi.fn(() => Promise.resolve());

vi.mock('../Paper/PaperEditor', () => ({
  PaperEditor: ({
    paperId,
    embedded,
    ref,
  }: {
    paperId: string;
    embedded?: boolean;
    ref?: React.Ref<PaperEditorHandle>;
  }) => {
    useImperativeHandle(ref, () => ({ commitLocal }), []);
    return (
      <div data-testid="paper-editor" data-paper={paperId}>
        {embedded ? 'embedded' : 'standalone'}
      </div>
    );
  },
}));

function source(overrides: Partial<StudySource> = {}): StudySource {
  return {
    id: 's1',
    title: 'Write-Ahead Logging',
    kind: 'web',
    sourceUrl: 'https://www.sqlite.org/wal.html',
    contentType: 'text/html',
    sizeBytes: 4096,
    durationSeconds: null,
    notePath: 'study/wal.md',
    importStatus: 'ready',
    importError: null,
    lastOpenedAt: null,
    position: null,
    paperId: null,
    noteMode: 'note',
    createdAt: '2026-09-04T10:00:00+00:00',
    updatedAt: '2026-09-04T10:00:00+00:00',
    ...overrides,
  };
}

function renderDesk(initial: StudySource, onBack = () => {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return render(
    <StudyDesk sourceId={initial.id} initial={initial} onBack={onBack} />,
    { wrapper: Wrapper }
  );
}

beforeEach(() => {
  commitLocal.mockClear();
  vi.spyOn(api.study, 'source').mockImplementation(id =>
    Promise.resolve(source({ id }))
  );
  vi.spyOn(api.study, 'update').mockImplementation((id, updates) =>
    Promise.resolve(source({ id, ...updates } as Partial<StudySource>))
  );
  vi.spyOn(api.paper, 'create').mockResolvedValue({
    id: 'p-new',
    pageId: 'pg-new',
  });
  // The note pane mounts the real Notebook editor, which reads its file.
  vi.spyOn(api.notebook.files, 'read').mockResolvedValue({ content: '' });
  vi.spyOn(api.notebook.files, 'ensure').mockResolvedValue(undefined);
  vi.spyOn(api.notebook.review, 'getState').mockResolvedValue({
    path: 'study/wal.md',
    enabled: false,
  } as never);
});

afterEach(() => vi.restoreAllMocks());

describe('the desk’s two note modes', () => {
  it('opens on the mode the source was last studied with', async () => {
    renderDesk(source({ noteMode: 'paper', paperId: 'p1' }));

    const editor = await screen.findByTestId('paper-editor');
    expect(editor.getAttribute('data-paper')).toBe('p1');
    // Embedded: no immersive claim, and no paper-level Back or "to journal".
    expect(editor.textContent).toBe('embedded');
  });

  it('remembers a switch without re-rendering the source', async () => {
    renderDesk(source());
    await waitFor(() => expect(screen.getByText('⌨ Notes')).toBeTruthy());
    expect(screen.queryByTestId('paper-editor')).toBeNull();

    fireEvent.click(screen.getByText('✎ Paper'));

    await screen.findByTestId('paper-editor');
    await waitFor(() =>
      expect(api.study.update).toHaveBeenCalledWith('s1', {
        noteMode: 'paper',
      })
    );
  });

  it('makes the paper on the first switch, and only once', async () => {
    renderDesk(source());
    await waitFor(() => expect(screen.getByText('✎ Paper')).toBeTruthy());

    fireEvent.click(screen.getByText('✎ Paper'));

    // Minted client-side so the page exists with no wifi, then bound.
    await waitFor(() => expect(api.paper.create).toHaveBeenCalledTimes(1));
    const created = vi.mocked(api.paper.create).mock.calls[0]?.[0];
    expect(created).toBeTruthy();
    await waitFor(() =>
      expect(api.study.update).toHaveBeenCalledWith('s1', {
        paperId: created!.id,
      })
    );
    const editor = await screen.findByTestId('paper-editor');
    expect(editor.getAttribute('data-paper')).toBe(created!.id);
  });

  it('does not make a second paper for a source that has one', async () => {
    renderDesk(source({ noteMode: 'paper', paperId: 'p1' }));
    await screen.findByTestId('paper-editor');
    expect(api.paper.create).not.toHaveBeenCalled();
  });
});

describe('leaving the desk with a paper open', () => {
  it('commits the open page before it stops rendering the editor', async () => {
    // The editor has no unmount commit of its own and cannot have one: a
    // passive cleanup runs after React has detached the canvas ref, so there
    // is nothing left to read the strokes from.
    const onBack = vi.fn();
    renderDesk(source({ noteMode: 'paper', paperId: 'p1' }), onBack);
    await screen.findByTestId('paper-editor');

    fireEvent.click(screen.getByText('‹ Sources'));

    expect(commitLocal).toHaveBeenCalledTimes(1);
    expect(onBack).toHaveBeenCalled();
  });

  it('commits it when switching back to the notes, too', async () => {
    renderDesk(source({ noteMode: 'paper', paperId: 'p1' }));
    await screen.findByTestId('paper-editor');

    fireEvent.click(screen.getByText('⌨ Notes'));

    expect(commitLocal).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(screen.queryByTestId('paper-editor')).toBeNull()
    );
  });
});

describe('binding the paper', () => {
  it('binds the paper only once the server has it', async () => {
    // `paper_id` is a real foreign key: a PATCH that overtakes the POST is
    // answered "No such paper" and the binding is lost — the pane would work
    // for this session and open on the text editor the next time.
    let releaseCreate = () => {};
    vi.mocked(api.paper.create).mockReturnValue(
      new Promise(resolve => {
        releaseCreate = () => resolve({ id: 'p-new', pageId: 'pg-new' });
      })
    );
    const boundWith = () =>
      vi
        .mocked(api.study.update)
        .mock.calls.filter(([, updates]) => 'paperId' in updates);

    renderDesk(source());
    await waitFor(() => expect(screen.getByText('✎ Paper')).toBeTruthy());
    fireEvent.click(screen.getByText('✎ Paper'));

    // The editor is already up — the paper exists on this device the moment
    // its id is minted, which is what makes it usable with no wifi.
    await screen.findByTestId('paper-editor');
    expect(boundWith()).toHaveLength(0);

    await act(async () => {
      releaseCreate();
    });
    await waitFor(() => expect(boundWith()).toHaveLength(1));
  });
});

// The desk is handed its row by `initialData` and by the *persisted* query
// cache, so what it renders has never necessarily been near the network. A
// row of nulls -- a real one, seen in the wild -- used to read as "a source
// with no note and no paper": the note pane slugged `null.toLowerCase()` and
// took the whole app down with it, and on the other mode the paper pane made a
// second, orphaned paper.
describe('a row of nulls where a source should be', () => {
  const nulls = Object.fromEntries(
    Object.keys(source()).map(k => [k, null])
  ) as unknown as StudySource;

  it('waits for a real source instead of taking the app down', async () => {
    const errors: unknown[] = [];
    const onError = (e: ErrorEvent) => errors.push(e.error ?? e.message);
    window.addEventListener('error', onError);
    try {
      render(
        <QueryClientProvider
          client={
            new QueryClient({ defaultOptions: { queries: { retry: false } } })
          }
        >
          <StudyDesk sourceId="s1" initial={nulls} onBack={() => {}} />
        </QueryClientProvider>
      );
      await waitFor(() => expect(screen.getByText('Loading…')).toBeTruthy());
      await act(async () => {
        await new Promise(r => setTimeout(r, 20));
      });
    } finally {
      window.removeEventListener('error', onError);
    }

    expect(errors).toEqual([]);
    // Neither pane ran, so nothing was created against a source that isn't one.
    expect(api.notebook.files.ensure).not.toHaveBeenCalled();
    expect(api.paper.create).not.toHaveBeenCalled();
  });

  it('never lets one into the cache from an update response', async () => {
    vi.mocked(api.study.update).mockResolvedValue(nulls);
    renderDesk(source({ noteMode: 'note', notePath: 'study/wal.md' }));
    await waitFor(() => expect(screen.getByText('✎ Paper')).toBeTruthy());

    fireEvent.click(screen.getByText('✎ Paper'));

    // The PATCH answered with rubbish; the desk keeps the source it had, so
    // the title is still on screen and the pane still has its paper to make.
    await waitFor(() =>
      expect(api.study.update).toHaveBeenCalledWith('s1', {
        noteMode: 'paper',
      })
    );
    await waitFor(() =>
      expect(screen.getByText('Write-Ahead Logging')).toBeTruthy()
    );
  });
});
