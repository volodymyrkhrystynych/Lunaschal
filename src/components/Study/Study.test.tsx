// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
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
import { Study } from './Study';

// pdf.js reaches for a Worker and a canvas 2d context, neither of which jsdom
// has. Most of these tests are not about the viewer's own rendering, so the
// document is empty by default and `pdfPages` opts a test into a real one.
//
// The 2d context stub is load-bearing, not tidiness: `PdfViewer` does
// `if (!context) continue;`, so with jsdom's null context the render loop
// appends nothing and a restore test would pass without restoring anything.
let pdfPages = 0;

vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: { workerSrc: '' },
  getDocument: () => ({
    promise: Promise.resolve({
      get numPages() {
        return pdfPages;
      },
      getPage: () =>
        Promise.resolve({
          getViewport: () => ({ width: 100, height: 140 }),
          render: () => ({ promise: Promise.resolve() }),
        }),
      destroy: () => Promise.resolve(),
    }),
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
    position: null,
    paperId: null,
    noteMode: 'note' as const,
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

// The desk only exists at >=1024px now (Study.tsx), and src/test/setup.ts's
// default matchMedia answers `false` to everything — so without this every
// desk test would be looking at the library instead.
//
// The stub keeps its listeners rather than dropping them, because that is the
// only way a *resize* can be simulated: `useMediaQuery`'s effect keys on the
// query string, so re-rendering re-reads nothing. A change event is what a
// dragged window boundary actually looks like to it.
let large = true;
const mediaListeners = new Set<{
  query: string;
  fire: (matches: boolean) => void;
}>();

function setViewport(size: 'large' | 'small') {
  large = size === 'large';
  window.matchMedia = ((query: string) => {
    const matches = () => (large ? query.includes('min-width') : false);
    return {
      get matches() {
        return matches();
      },
      media: query,
      onchange: null,
      addEventListener: (_: string, cb: (e: MediaQueryListEvent) => void) => {
        mediaListeners.add({
          query,
          fire: m => cb({ matches: m } as MediaQueryListEvent),
        });
      },
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    } as unknown as MediaQueryList;
  }) as typeof window.matchMedia;
  for (const l of mediaListeners) {
    l.fire(large ? l.query.includes('min-width') : false);
  }
}

beforeEach(() => {
  mediaListeners.clear();
  setViewport('large');
  pdfPages = 0;
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
    {} as unknown as CanvasRenderingContext2D
  );
  vi.spyOn(api.study, 'update').mockResolvedValue(source());
  // The note pane mounts the real Notebook editor, which reads its file.
  vi.spyOn(api.notebook.files, 'read').mockResolvedValue({ content: '' });
  vi.spyOn(api.notebook.files, 'ensure').mockResolvedValue(undefined);
  vi.spyOn(api.notebook.review, 'getState').mockResolvedValue({
    path: 'study/attention.md',
    enabled: false,
  } as never);
});

afterEach(() => {
  vi.restoreAllMocks();
  // `Object.defineProperty` is not a mock, so restoreAllMocks won't undo it.
  delete (HTMLElement.prototype as { scrollTo?: unknown }).scrollTo;
});

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

  it('says why a video on an unplugged drive cannot be opened', async () => {
    // Videos live on the external archive drive and nowhere else, so an
    // unplugged drive leaves the row browsable and the file gone. The row must
    // explain that rather than open a <video> pointed at a 404.
    vi.spyOn(api.study, 'sources').mockResolvedValue([
      source({
        kind: 'youtube',
        title: 'Lecture 1: Backprop',
        durationSeconds: 3771,
        fileAvailable: false,
        fileUnavailableReason: 'The backup drive is not connected.',
      }),
    ]);
    renderStudy();

    await waitFor(() =>
      expect(screen.getByText('Lecture 1: Backprop')).toBeTruthy()
    );
    expect(
      screen.getAllByText('The backup drive is not connected.').length
    ).toBeGreaterThan(0);

    // Still openable — the desk is where the explanation lives.
    fireEvent.click(screen.getByText('Lecture 1: Backprop'));
    await waitFor(() =>
      expect(screen.getByText(/kept on the archive drive/)).toBeTruthy()
    );
    expect(document.querySelector('video')).toBeNull();
  });

  it('retries a failed import in place instead of beside itself', async () => {
    vi.spyOn(api.study, 'sources').mockResolvedValue([
      source({
        id: 'failed',
        kind: 'youtube',
        importStatus: 'error',
        importError: 'The backup drive is not connected.',
        sourceUrl: 'https://youtu.be/abc',
      }),
    ]);
    const importYoutube = vi
      .spyOn(api.study, 'importYoutube')
      .mockResolvedValue({ id: 's9', source: source({ kind: 'youtube' }) });
    const remove = vi
      .spyOn(api.study, 'remove')
      .mockResolvedValue({ success: true });
    renderStudy();

    await waitFor(() => expect(screen.getByText('Retry')).toBeTruthy());
    fireEvent.click(screen.getByText('Retry'));

    await waitFor(() =>
      expect(importYoutube).toHaveBeenCalledWith('https://youtu.be/abc')
    );
    // The row it replaces goes only once the replacement is under way.
    await waitFor(() => expect(remove).toHaveBeenCalledWith('failed'));
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

  it('opens a PDF on the page it was left on', async () => {
    pdfPages = 6;
    const saved = source({ position: 4 });
    vi.spyOn(api.study, 'sources').mockResolvedValue([saved]);
    vi.spyOn(api.study, 'source').mockResolvedValue(saved);
    // jsdom lays nothing out, so give the canvases distinguishable offsets and
    // record where the scroller was asked to go.
    // jsdom implements no scrolling at all, so `scrollTo` has to be defined
    // rather than spied on.
    const scrolled: number[] = [];
    Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
      configurable: true,
      writable: true,
      value: (arg: { top: number }) => scrolled.push(arg.top),
    });
    vi.spyOn(HTMLElement.prototype, 'offsetTop', 'get').mockImplementation(
      function (this: HTMLElement) {
        const page = Number((this as HTMLCanvasElement).dataset?.page ?? 0);
        return page * 200;
      }
    );
    renderStudy();

    await waitFor(() =>
      expect(screen.getByText('Attention Is All You Need')).toBeTruthy()
    );
    fireEvent.click(screen.getByText('Attention Is All You Need'));

    // Page 4's canvas sits at 800; the host div's own offsetTop is 0.
    await waitFor(() => expect(scrolled).toContain(800));
    await waitFor(() => expect(screen.getByText('4 / 6')).toBeTruthy());
  });

  it('resumes a video at the second it was left on', async () => {
    const saved = source({
      kind: 'youtube',
      position: 1830.5,
      durationSeconds: 3771,
    });
    vi.spyOn(api.study, 'sources').mockResolvedValue([saved]);
    vi.spyOn(api.study, 'source').mockResolvedValue(saved);
    renderStudy();

    await waitFor(() =>
      expect(screen.getByText('Attention Is All You Need')).toBeTruthy()
    );
    fireEvent.click(screen.getByText('Attention Is All You Need'));

    const video = await waitFor(() => {
      const el = document.querySelector('video');
      if (!el) throw new Error('no video');
      return el;
    });
    // jsdom has no media pipeline, but `duration` is readable and
    // `currentTime` is a real settable property.
    Object.defineProperty(video, 'duration', { value: 3771, writable: true });
    fireEvent(video, new Event('loadedmetadata'));

    expect(video.currentTime).toBe(1830.5);
  });

  it('writes one position however many pages go by', async () => {
    vi.useFakeTimers();
    try {
      pdfPages = 6;
      vi.spyOn(api.study, 'sources').mockResolvedValue([source()]);
      vi.spyOn(api.study, 'source').mockResolvedValue(source());
      vi.spyOn(HTMLElement.prototype, 'offsetTop', 'get').mockImplementation(
        function (this: HTMLElement) {
          const page = Number((this as HTMLCanvasElement).dataset?.page ?? 0);
          return page * 200;
        }
      );
      renderStudy();

      await vi.waitFor(() =>
        expect(screen.getByText('Attention Is All You Need')).toBeTruthy()
      );
      fireEvent.click(screen.getByText('Attention Is All You Need'));
      const scroller = await vi.waitFor(() => {
        const el =
          document.querySelector('canvas')?.parentElement?.parentElement;
        if (!el) throw new Error('no scroller');
        return el;
      });

      // Six scroll events, walking down the document.
      for (const top of [200, 400, 600, 800, 1000, 1200]) {
        Object.defineProperty(scroller, 'scrollTop', {
          value: top,
          configurable: true,
        });
        fireEvent.scroll(scroller);
      }
      const before = vi
        .mocked(api.study.update)
        .mock.calls.filter(c => 'position' in (c[1] ?? {}));
      expect(before).toHaveLength(0);

      await vi.advanceTimersByTimeAsync(2000);

      const writes = vi
        .mocked(api.study.update)
        .mock.calls.filter(c => 'position' in (c[1] ?? {}));
      expect(writes).toHaveLength(1);
      expect(writes[0][1].position).toBe(6);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('Study on a screen too small for the desk', () => {
  // Below 1024px the tab is an import queue and nothing else: somewhere to
  // drop a YouTube link from the phone you found it on. It used to be hidden
  // entirely, which took the queueing half away along with the reading half.
  beforeEach(() => setViewport('small'));

  it('still offers every way in', async () => {
    vi.spyOn(api.study, 'sources').mockResolvedValue([]);
    renderStudy();

    await waitFor(() => expect(screen.getByText('📕 Upload PDF')).toBeTruthy());
    expect(screen.getByText('🌐 Import website')).toBeTruthy();
    expect(screen.getByText('🎬 Import YouTube')).toBeTruthy();
    expect(await screen.findByText(/read it at the desk/)).toBeTruthy();
  });

  it('imports a YouTube link the same way the wide screen does', async () => {
    vi.spyOn(api.study, 'sources').mockResolvedValue([]);
    const importYoutube = vi
      .spyOn(api.study, 'importYoutube')
      .mockResolvedValue({ id: 'new', source: source({ id: 'new' }) });
    renderStudy();

    await waitFor(() =>
      expect(screen.getByText('🎬 Import YouTube')).toBeTruthy()
    );
    fireEvent.click(screen.getByText('🎬 Import YouTube'));
    fireEvent.change(screen.getByLabelText('YouTube URL'), {
      target: { value: 'https://youtu.be/abc' },
    });
    fireEvent.click(screen.getByText('Import'));

    await waitFor(() =>
      expect(importYoutube).toHaveBeenCalledWith('https://youtu.be/abc')
    );
  });

  it('does not offer a row as something to press', async () => {
    vi.spyOn(api.study, 'sources').mockResolvedValue([source()]);
    renderStudy();

    const title = await screen.findByText('Attention Is All You Need');
    // Not a disabled button — no button at all. A row that looks pressable and
    // is not would be the whole list here.
    expect(title.closest('button')).toBeNull();
    // The delete control is still reachable: it no longer waits for a hover
    // that a touch screen cannot give it.
    expect(
      screen.getByLabelText('Delete Attention Is All You Need')
    ).toBeTruthy();
  });

  it('closes the desk when the window is dragged narrow', async () => {
    // A desk left open behind the library holds a source the library can
    // meanwhile delete, and re-widening into something you had left is worse
    // than one extra tap.
    setViewport('large');
    vi.spyOn(api.study, 'sources').mockResolvedValue([source()]);
    renderStudy();

    fireEvent.click(await screen.findByText('Attention Is All You Need'));
    await waitFor(() => expect(screen.getByText('‹ Sources')).toBeTruthy());

    // Narrowing fires the media query's own change event, which is what a
    // dragged window boundary does in a real browser.
    await act(async () => setViewport('small'));

    await waitFor(() => expect(screen.queryByText('‹ Sources')).toBeNull());
    expect(screen.getByText('📕 Upload PDF')).toBeTruthy();
  });
});
