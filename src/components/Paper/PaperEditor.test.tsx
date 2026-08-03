// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  render,
  waitFor,
  act,
  fireEvent,
  screen,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { PaperEditor } from './PaperEditor';
import { PAGE_HEIGHT, PAGE_WIDTH } from '@/lib/paper';

vi.mock('../../hooks/api', () => ({
  api: {
    paper: {
      get: vi.fn(),
      getPage: vi.fn(),
      savePage: vi.fn(),
      addPage: vi.fn(),
      setArchiveRequested: vi.fn(),
      updateImage: vi.fn(),
      addImage: vi.fn(),
      deleteImage: vi.fn(),
    },
  },
}));

vi.mock('idb-keyval', () => ({
  get: vi.fn(() => Promise.resolve(undefined)),
  set: vi.fn(() => Promise.resolve()),
  del: vi.fn(() => Promise.resolve()),
}));

const PAGE_1 = 'page-1';
const PAGE_2 = 'page-2';

beforeEach(() => {
  vi.clearAllMocks();
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
    setTransform: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    arc: vi.fn(),
    fill: vi.fn(),
    fillRect: vi.fn(),
    stroke: vi.fn(),
  })) as unknown as HTMLCanvasElement['getContext'];
  HTMLCanvasElement.prototype.toBlob = vi.fn(cb =>
    cb(new Blob(['x'], { type: 'image/png' }))
  );
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  // jsdom lays nothing out, so the drawing stage measures 0x0 and the editor
  // renders its "Loading…" branch forever. Give every element a size.
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    value: 800,
  });
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    value: 1000,
  });

  vi.mocked(api.paper.get).mockResolvedValue({
    id: 'doc-1',
    title: 'Notes',
    archiveRequested: false,
    pages: [
      { id: PAGE_1, position: 0 },
      { id: PAGE_2, position: 1 },
    ],
  } as never);
  // Both pages start empty, as a freshly created paper does.
  vi.mocked(api.paper.getPage).mockResolvedValue({
    strokes: '[]',
    width: null,
    height: null,
    images: [],
  });
  vi.mocked(api.paper.savePage).mockResolvedValue({ success: true } as never);
  vi.mocked(api.paper.updateImage).mockResolvedValue({} as never);
});

function renderEditor() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 1000 * 60 } },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <PaperEditor paperId="doc-1" onBack={() => {}} />
    </QueryClientProvider>
  );
  return { ...view, queryClient };
}

describe('page content cache after a save', () => {
  it("replaces the page's cached content with what was just uploaded", async () => {
    // The bug this pins: the cache entry kept the pre-save copy (empty, for a
    // page written on for the first time), so coming back to the page re-seeded
    // the canvas from it and the page read as blank. Worse, drawing on the
    // blank one would overwrite the real strokes, because a save replaces the
    // column outright.
    const { container, queryClient } = renderEditor();
    const canvas = await waitFor(() => {
      const c = container.querySelector('canvas');
      expect(c).toBeTruthy();
      return c!;
    });
    canvas.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: 400, height: 566 }) as DOMRect;

    expect(queryClient.getQueryData(['paper', 'page', PAGE_1])).toMatchObject({
      strokes: '[]',
    });

    // Draw, then explicitly save — nothing uploads on its own any more.
    await act(async () => {
      canvas.dispatchEvent(
        new MouseEvent('pointerdown', {
          bubbles: true,
          clientX: 20,
          clientY: 20,
        })
      );
      canvas.dispatchEvent(
        new MouseEvent('pointermove', {
          bubbles: true,
          clientX: 80,
          clientY: 120,
        })
      );
      canvas.dispatchEvent(
        new MouseEvent('pointerup', {
          bubbles: true,
          clientX: 80,
          clientY: 120,
        })
      );
    });

    expect(api.paper.savePage).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(api.paper.savePage).toHaveBeenCalled());

    const uploaded = vi.mocked(api.paper.savePage).mock.calls[0][1];
    // Guard the guard: an upload of "[]" would make the assertion below vacuous.
    expect(uploaded.strokes).not.toBe('[]');

    await waitFor(() =>
      expect(queryClient.getQueryData(['paper', 'page', PAGE_1])).toEqual({
        strokes: uploaded.strokes,
        width: PAGE_WIDTH,
        height: PAGE_HEIGHT,
        // A stroke save must not blank the page's pictures.
        images: [],
      })
    );
    // And the refresh is free: writing the known answer in must not cost a
    // refetch of the page currently under the pen.
    expect(api.paper.getPage).toHaveBeenCalledTimes(1);
  });
});

describe('manual save', () => {
  it('stages a moved picture locally and only PATCHes it on save', async () => {
    // A full-page image so any click inside the drawing area lands on it,
    // sidestepping the need to replicate PaperEditor's fitPageBox math here.
    vi.mocked(api.paper.getPage).mockResolvedValue({
      strokes: '[]',
      width: null,
      height: null,
      images: [
        {
          id: 'img-1',
          url: '/api/paper/images/img-1/file',
          x: 0,
          y: 0,
          width: PAGE_WIDTH,
          height: PAGE_HEIGHT,
          rotation: 0,
          flipped: 0,
          locked: 0,
          position: 0,
        },
      ],
    } as never);

    const { container } = renderEditor();
    await waitFor(() => expect(container.querySelector('canvas')).toBeTruthy());

    fireEvent.click(
      screen.getByTitle('Select and move pictures instead of drawing')
    );
    const layer = await waitFor(() => screen.getByTestId('paper-image-layer'));

    fireEvent.pointerDown(layer, { clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(layer, { clientX: 150, clientY: 160, pointerId: 1 });
    fireEvent.pointerUp(layer, { clientX: 150, clientY: 160, pointerId: 1 });

    // The drag committed locally, not to the server — moving a picture must
    // not sync on its own any more than drawing does.
    expect(api.paper.updateImage).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(api.paper.updateImage).toHaveBeenCalledTimes(1));
    expect(api.paper.updateImage).toHaveBeenCalledWith(
      'img-1',
      expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) })
    );
  });
});
