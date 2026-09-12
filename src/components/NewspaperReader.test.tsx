// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api } from '../hooks/api';
import { NewspaperReader } from './NewspaperReader';

vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: {},
  getDocument: () => ({
    promise: Promise.resolve({
      numPages: 1,
      // Enough of a page for the thumbnail worker: an aspect ratio and a
      // render that resolves. What the JPEG would look like is untestable
      // here — jsdom rasterizes nothing — so what these tests pin is which
      // pages are rendered and when.
      getPage: vi.fn(async () => ({
        getViewport: ({ scale }: { scale: number }) => ({
          width: 612 * scale,
          height: 792 * scale,
        }),
        render: () => ({ promise: Promise.resolve() }),
      })),
    }),
    destroy: vi.fn(),
  }),
}));
vi.mock('../hooks/api', () => ({
  // Carries a status, like the real one: the reader has to tell a 409 (another
  // reader moved the markup on) from any other refusal, and a bare Error cannot.
  ApiError: class extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
  api: {
    newspapers: {
      markup: vi.fn(),
      saveMarkup: vi.fn(),
      markOpened: vi.fn(),
      pageImages: vi.fn(),
      savePageImage: vi.fn(),
    },
  },
}));

const issue = {
  date: '2026-09-01',
  byteSize: 1000,
  pageCount: 1,
  pdfUrl: '/issue.pdf',
};
const draftKey = 'newspaper-markup:2026-09-01';
const stroke = {
  page: 1,
  tool: 'pen',
  points: [
    [0.1, 0.2],
    [0.3, 0.4],
  ],
};

function stubContext() {
  return {
    setTransform: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    arc: vi.fn(),
    fill: vi.fn(),
    fillRect: vi.fn(),
    clearRect: vi.fn(),
    stroke: vi.fn(),
    fillStyle: '',
    strokeStyle: '',
    lineWidth: 0,
    lineCap: '',
    lineJoin: '',
    globalAlpha: 1,
  };
}

let ctx: ReturnType<typeof stubContext>;

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  ctx = stubContext();
  HTMLCanvasElement.prototype.getContext = vi.fn(
    () => ctx
  ) as unknown as HTMLCanvasElement['getContext'];
  // jsdom lays nothing out, so the ink layer needs a box for a pointer to land
  // anywhere but (0, 0). On Element, not HTMLCanvasElement: the markup layer is
  // an <svg> drawn over the PDF's canvas.
  Element.prototype.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 100, height: 200 }) as DOMRect;
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      // The ink layer is mounted only for pages near the viewport, so a page
      // that never reports itself visible has nothing to draw on.
      constructor(cb: (entries: { isIntersecting: boolean }[]) => void) {
        cb([{ isIntersecting: true }]);
      }
      observe() {}
      disconnect() {}
    }
  );
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    }
  );
  vi.mocked(api.newspapers.markup).mockResolvedValue({
    revision: 0,
    strokes: [],
  });
  vi.mocked(api.newspapers.saveMarkup).mockResolvedValue({ revision: 1 });
  vi.mocked(api.newspapers.markOpened).mockResolvedValue({ ok: true });
  vi.mocked(api.newspapers.pageImages).mockResolvedValue({ pages: [] });
  vi.mocked(api.newspapers.savePageImage).mockResolvedValue({
    page: 1,
    url: '/api/newspapers/issues/2026-09-01/pages/1?v=1',
    updatedAt: 1,
  });
  HTMLCanvasElement.prototype.toBlob = vi.fn(cb =>
    cb(new Blob(['x'], { type: 'image/jpeg' }))
  );
});

/** The reader's page surface, wired up the way a browser would wire it. */
async function openPage() {
  const { container } = render(
    <NewspaperReader issue={issue} onClose={vi.fn()} />
  );
  await screen.findByText('Save now');
  // The ink layer, told apart from the PDF canvas it is drawn over.
  const ink = await screen.findByLabelText('Page 1 markup');
  /** Marks actually on the page. The stroke in flight is a bare element with no
   * path data until a frame has run, so it is not counted. */
  const marks = () =>
    Array.from(ink.querySelectorAll('[data-ink-strokes] path')).filter(p =>
      p.getAttribute('d')
    );
  function pointer(name: string, type: string, x: number, y: number) {
    const event = new Event(name, { bubbles: true });
    Object.assign(event, {
      pointerType: type,
      pointerId: type === 'touch' ? 1 : 2,
      isPrimary: true,
      clientX: x,
      clientY: y,
    });
    fireEvent(ink, event);
  }
  // The page div is what carries the non-passive touchmove listener; jsdom has
  // no Touch constructor, so the touches are plain objects.
  function touchMove(...types: ('direct' | 'stylus')[]) {
    const event = new Event('touchmove', { bubbles: true, cancelable: true });
    Object.assign(event, {
      touches: types.map(touchType => ({ touchType, clientX: 0, clientY: 0 })),
    });
    // The page box carries the guard, not the ink layer: it stays mounted when
    // the canvas does not.
    screen.getByLabelText('Page 1').dispatchEvent(event);
    return event.defaultPrevented;
  }
  /** The tools moved into the floating panel, where they are icons with
   * accessible names rather than text. */
  const pick = (name: string) =>
    fireEvent.click(screen.getByRole('button', { name }));
  return { ink, marks, pointer, touchMove, pick };
}

describe('opening an issue', () => {
  it('tells the server, which is what dates the issue in the Journal', async () => {
    render(<NewspaperReader issue={issue} onClose={vi.fn()} />);
    await waitFor(() =>
      expect(api.newspapers.markOpened).toHaveBeenCalledWith('2026-09-01')
    );
  });

  it('still opens when the server cannot be told', async () => {
    // The stamp is a nicety; a reader offline on a train still has a paper.
    vi.mocked(api.newspapers.markOpened).mockRejectedValue(
      new Error('offline')
    );
    render(<NewspaperReader issue={issue} onClose={vi.fn()} />);
    expect(await screen.findByText('Save now')).toBeTruthy();
  });
});

describe('newspaper pencil and finger input', () => {
  it('records Pencil coordinates and ignores finger pointers entirely', async () => {
    const { marks, pointer, pick } = await openPage();
    pick('Pen');
    pointer('pointerdown', 'touch', 10, 20);
    pointer('pointermove', 'touch', 10, 40);
    pointer('pointerup', 'touch', 10, 40);
    // Nothing was marked: a finger scrolls, it never writes.
    expect(marks()).toHaveLength(0);
    pointer('pointerdown', 'pen', 10, 20);
    pointer('pointermove', 'pen', 40, 60);
    pointer('pointerup', 'pen', 40, 60);
    fireEvent.click(screen.getByText('Save now'));
    await waitFor(() =>
      expect(api.newspapers.saveMarkup).toHaveBeenCalledWith(issue.date, {
        revision: 0,
        strokes: [
          {
            page: 1,
            tool: 'pen',
            // Pressure rides along as a third coordinate; a pointer with none
            // reports 0.5, as a mouse does. The width is the medium pen and the
            // colour the one this reader has always drawn in.
            points: [
              [0.1, 0.1, 0.5],
              [0.4, 0.3, 0.5],
            ],
            size: 3,
            color: '#1756ad',
          },
        ],
      })
    );
  });

  it('cancels a stylus touch so the Pencil writes instead of scrolling', async () => {
    const { touchMove, pick } = await openPage();
    pick('Pen');
    expect(touchMove('stylus')).toBe(true);
  });

  it('leaves finger scrolling to the browser while marking', async () => {
    const { touchMove, pick } = await openPage();
    pick('Highlighter');
    expect(touchMove('direct')).toBe(false);
    // A palm beside the nib must not scroll either, but only two fingers can
    // pinch-zoom, so a stylus riding along with a finger is not cancelled.
    expect(touchMove('direct', 'stylus')).toBe(false);
  });

  it('lets the Pencil scroll again in Read mode', async () => {
    const { touchMove } = await openPage();
    expect(
      screen.getByRole('button', { name: 'Read' }).getAttribute('aria-pressed')
    ).toBe('true');
    expect(touchMove('stylus')).toBe(false);
  });

  it('holds every touch off while a stroke is in progress', async () => {
    const { pointer, touchMove, pick } = await openPage();
    pick('Pen');
    pointer('pointerdown', 'pen', 10, 20);
    // A resting palm would otherwise drag the page out from under the nib.
    expect(touchMove('direct')).toBe(true);
    pointer('pointerup', 'pen', 10, 20);
    expect(touchMove('direct')).toBe(false);
  });

  it('leaves the toolbar unchanged across an ordinary save', async () => {
    // The bar wraps, so anything that grows or appears in it costs a line and
    // gives it back. A stroke used to write a long status *and* reveal a close
    // button, so the bar changed height twice per stroke — which is unusable
    // to write against.
    const { pointer, pick } = await openPage();
    pick('Pen');
    pointer('pointerdown', 'pen', 10, 20);
    pointer('pointerup', 'pen', 10, 20);
    expect(screen.queryByText('Close with local draft')).toBeNull();
    expect(screen.getByRole('status').textContent).not.toMatch(/iPad/);
    fireEvent.click(screen.getByText('Save now'));
    expect(screen.queryByText('Close with local draft')).toBeNull();
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toBe('Saved')
    );
    expect(screen.queryByText('Close with local draft')).toBeNull();
  });

  it('never lets a finger scroll be blocked in Read mode', async () => {
    const { touchMove } = await openPage();
    expect(touchMove('direct')).toBe(false);
  });
});

describe('newspaper markup recovery', () => {
  it('recovers a local draft and clears it only after server acknowledgement', async () => {
    localStorage.setItem(
      draftKey,
      JSON.stringify({ revision: 0, strokes: [stroke] })
    );
    render(<NewspaperReader issue={issue} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByText('Save now'));
    // Read in the old shape, written back in the current one: an unpressured
    // point becomes full pressure, which is the width it was always drawn at.
    await waitFor(() =>
      expect(api.newspapers.saveMarkup).toHaveBeenCalledWith(issue.date, {
        revision: 0,
        strokes: [
          {
            page: 1,
            tool: 'pen',
            points: [
              [0.1, 0.2, 1],
              [0.3, 0.4, 1],
            ],
            size: 2,
          },
        ],
      })
    );
    await waitFor(() => expect(localStorage.getItem(draftKey)).toBeNull());
    expect(screen.getByRole('status').textContent).toBe('Saved');
  });

  it('retains a draft after a failed save and allows closing with the local copy', async () => {
    localStorage.setItem(
      draftKey,
      JSON.stringify({ revision: 0, strokes: [stroke] })
    );
    vi.mocked(api.newspapers.saveMarkup).mockRejectedValue(
      new Error('Offline')
    );
    const close = vi.fn();
    render(<NewspaperReader issue={issue} onClose={close} />);
    fireEvent.click(await screen.findByText('Save now'));
    await screen.findByText('Not saved to server: Offline');
    expect(localStorage.getItem(draftKey)).not.toBeNull();
    fireEvent.click(screen.getByText('Close with local draft'));
    expect(close).toHaveBeenCalled();
  });

  it('does not overwrite newer server markup with a stale recovered draft', async () => {
    localStorage.setItem(
      draftKey,
      JSON.stringify({ revision: 0, strokes: [stroke] })
    );
    vi.mocked(api.newspapers.markup).mockResolvedValue({
      revision: 2,
      strokes: [],
    });
    render(<NewspaperReader issue={issue} onClose={vi.fn()} />);
    await screen.findByText(/local draft conflicts/);
    fireEvent.click(screen.getByText('Save now'));
    expect(api.newspapers.saveMarkup).not.toHaveBeenCalled();
    expect(localStorage.getItem(draftKey)).not.toBeNull();
  });

  it('undoes a stroke made in this session, and saves the result', async () => {
    const { pointer, pick } = await openPage();
    pick('Pen');
    pointer('pointerdown', 'pen', 10, 20);
    pointer('pointermove', 'pen', 40, 60);
    pointer('pointerup', 'pen', 40, 60);

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    fireEvent.click(screen.getByText('Save now'));
    await waitFor(() =>
      expect(api.newspapers.saveMarkup).toHaveBeenCalledWith(issue.date, {
        revision: 0,
        strokes: [],
      })
    );
  });

  it('cannot rewind markup made in an earlier sitting', async () => {
    // Undo used to be `strokes.slice(0, -1)` over the whole issue, so it could
    // peel a stroke off the stored record — including one on a page that was
    // not even on screen. It is a session history now, like the Paper editor's,
    // and the eraser is the answer for ink from an earlier sitting. That the
    // eraser is page-local is the other half of the fix.
    vi.mocked(api.newspapers.markup).mockResolvedValue({
      revision: 3,
      strokes: [stroke],
    } as never);
    render(<NewspaperReader issue={issue} onClose={vi.fn()} />);
    await screen.findByText('Save now');
    expect(
      screen.getByRole('button', { name: 'Undo' }).hasAttribute('disabled')
    ).toBe(true);
  });
});

describe('a conflicting save', () => {
  // The server answers 409 when another reader has already moved the markup on
  // (newspaper_issues.py's compare-and-swap on `revision`). Until this was
  // fixed the reader could not see that: `saveMarkup` went through the plain
  // `send`, which throws an `Error` with no status, so `e instanceof ApiError`
  // was never true. The conflict flag stayed false, "Use server copy" never
  // appeared, and the 1.5 s autosave re-sent the same stale revision forever.
  it('offers the server copy and stops re-sending the stale revision', async () => {
    vi.mocked(api.newspapers.saveMarkup).mockRejectedValue(
      new ApiError('Markup changed in another reader.', 409)
    );
    const { pointer, pick } = await openPage();
    pick('Pen');
    pointer('pointerdown', 'pen', 10, 20);
    pointer('pointerup', 'pen', 10, 20);

    fireEvent.click(screen.getByText('Save now'));
    await screen.findByText('Use server copy');
    const attempts = vi.mocked(api.newspapers.saveMarkup).mock.calls.length;

    // Every later save is refused before it reaches the network, so the loop
    // cannot keep hammering a request the server has already decided on.
    fireEvent.click(screen.getByText('Save now'));
    fireEvent.click(screen.getByText('Save now'));
    await waitFor(() =>
      expect(api.newspapers.saveMarkup).toHaveBeenCalledTimes(attempts)
    );
  });
});

describe('the eraser the reader never had', () => {
  it('rubs out a stroke and saves what survived', async () => {
    const { pointer, pick } = await openPage();
    pick('Pen');
    pointer('pointerdown', 'pen', 10, 20);
    pointer('pointermove', 'pen', 40, 60);
    pointer('pointerup', 'pen', 40, 60);

    pick('Eraser');
    // Scrubbed straight over the stroke that was just drawn.
    pointer('pointerdown', 'pen', 10, 20);
    pointer('pointermove', 'pen', 40, 60);
    pointer('pointerup', 'pen', 40, 60);

    fireEvent.click(screen.getByText('Save now'));
    await waitFor(() =>
      expect(api.newspapers.saveMarkup).toHaveBeenCalledWith(issue.date, {
        revision: 0,
        strokes: [],
      })
    );
  });

  it('does not spend an undo step on a scrub that touched nothing', async () => {
    const { pointer, pick } = await openPage();
    pick('Eraser');
    pointer('pointerdown', 'pen', 10, 20);
    pointer('pointermove', 'pen', 40, 60);
    pointer('pointerup', 'pen', 40, 60);
    expect(
      screen.getByRole('button', { name: 'Undo' }).hasAttribute('disabled')
    ).toBe(true);
  });

  it('never lays down ink of its own', async () => {
    // The eraser stroke is consumed, not stored: it removes what it crossed and
    // leaves nothing behind. That is also what lets the markup layer be
    // transparent — there is no "paint over it in the page colour" anywhere.
    const { pointer, pick } = await openPage();
    pick('Pen');
    pointer('pointerdown', 'pen', 10, 20);
    pointer('pointermove', 'pen', 40, 60);
    pointer('pointerup', 'pen', 40, 60);

    pick('Eraser');
    // Scrubbed well clear of the stroke, so nothing is removed either.
    pointer('pointerdown', 'pen', 80, 180);
    pointer('pointermove', 'pen', 90, 190);
    pointer('pointerup', 'pen', 90, 190);

    fireEvent.click(screen.getByText('Save now'));
    await waitFor(() => expect(api.newspapers.saveMarkup).toHaveBeenCalled());
    const [, sent] = vi.mocked(api.newspapers.saveMarkup).mock.calls[0];
    expect(sent.strokes.map(s => s.tool)).toEqual(['pen']);
  });
});

describe('an issue that has reached its markup limit', () => {
  it('takes a refused mark back off the page instead of drawing a lie', async () => {
    // The ink layer commits a stroke locally the moment the pen lifts, and only
    // then does the reader get to refuse it. Without pulling the surface back
    // into line, the refused mark stays on screen looking exactly like every
    // saved one, and is silently gone on the next open.
    // One short of the cap, so the first mark lands and the second cannot.
    const nearlyFull = Array.from({ length: 9999 }, (_, i) => ({
      page: 1,
      tool: 'pen',
      points: [[i / 10000, 0.5]],
    }));
    vi.mocked(api.newspapers.markup).mockResolvedValue({
      revision: 0,
      strokes: nearlyFull,
    } as never);

    const { marks, pointer, pick } = await openPage();
    pick('Pen');
    const draw = (from: number) => {
      pointer('pointerdown', 'pen', from, 20);
      pointer('pointermove', 'pen', from + 30, 60);
      pointer('pointerup', 'pen', from + 30, 60);
    };
    draw(10);
    expect(screen.queryByText(/reached its markup limit/)).toBeNull();

    const before = marks().length;
    draw(50);
    await screen.findByText(/reached its markup limit/);
    // The page shows exactly what is stored, not the extra mark it had already
    // drawn and the reader then refused.
    expect(marks()).toHaveLength(before);
    fireEvent.click(screen.getByText('Save now'));
    await waitFor(() => expect(api.newspapers.saveMarkup).toHaveBeenCalled());
    const [, sent] = vi.mocked(api.newspapers.saveMarkup).mock.calls[0];
    expect(sent.strokes).toHaveLength(10000);
  });
});

describe('the pictures the Journal card shows', () => {
  // Made here because nothing on the server can draw a PDF page. What the JPEG
  // actually looks like is deliberately not asserted anywhere: jsdom has no
  // raster and no Path2D, so what is pinned is which pages get rendered, when,
  // and that a rendered one is uploaded.
  //
  // Fake timers throughout: the worker looks for work every three seconds, and
  // four tests waiting that out in real time is most of this suite's runtime.
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const serverHasCover = () =>
    vi.mocked(api.newspapers.pageImages).mockResolvedValue({
      pages: [
        {
          page: 1,
          url: '/api/newspapers/issues/2026-09-01/pages/1?v=9',
          updatedAt: 9,
        },
      ],
    });

  it('renders the cover of an issue the server has no picture of', async () => {
    await openPage();
    await vi.advanceTimersByTimeAsync(3500);
    expect(api.newspapers.savePageImage).toHaveBeenCalledWith(
      issue.date,
      1,
      expect.any(Blob)
    );
    // The cover, and nothing else: page 1 is the only page of this issue, and
    // once uploaded it is not wanted again.
    await vi.advanceTimersByTimeAsync(9000);
    expect(api.newspapers.savePageImage).toHaveBeenCalledTimes(1);
  });

  it('leaves alone a page the server already has a picture of', async () => {
    serverHasCover();
    await openPage();
    await vi.advanceTimersByTimeAsync(9000);
    expect(api.newspapers.savePageImage).not.toHaveBeenCalled();
  });

  it('renders a page again once it has been drawn on', async () => {
    serverHasCover();
    const { pointer, pick } = await openPage();
    pick('Pen');
    pointer('pointerdown', 'pen', 10, 20);
    pointer('pointermove', 'pen', 40, 60);
    pointer('pointerup', 'pen', 40, 60);
    await vi.advanceTimersByTimeAsync(3500);
    expect(api.newspapers.savePageImage).toHaveBeenCalledWith(
      issue.date,
      1,
      expect.any(Blob)
    );
  });

  it('sends no picture of strokes the server has refused', async () => {
    // A reader in conflict holds markup the server would not take; a picture of
    // it would land anyway, since a file has no revision to be refused on.
    serverHasCover();
    vi.mocked(api.newspapers.saveMarkup).mockRejectedValue(
      new ApiError('Markup changed in another reader.', 409)
    );
    const { pointer, pick } = await openPage();
    pick('Pen');
    pointer('pointerdown', 'pen', 10, 20);
    pointer('pointermove', 'pen', 40, 60);
    pointer('pointerup', 'pen', 40, 60);
    await vi.advanceTimersByTimeAsync(2000);
    await screen.findByText(/Use server copy/);
    vi.mocked(api.newspapers.savePageImage).mockClear();
    await vi.advanceTimersByTimeAsync(9000);
    expect(api.newspapers.savePageImage).not.toHaveBeenCalled();
  });

  it('still opens when the inventory cannot be fetched', async () => {
    vi.mocked(api.newspapers.pageImages).mockRejectedValue(
      new Error('offline')
    );
    await openPage();
    expect(await screen.findByText('Save now')).toBeTruthy();
  });
});
