// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  render,
  waitFor,
  act,
  fireEvent,
  screen,
} from '@testing-library/react';
import {
  QueryClient,
  QueryClientProvider,
  onlineManager,
} from '@tanstack/react-query';
import { api, ApiError } from '../../hooks/api';
import { PaperEditor } from './PaperEditor';
import { PAGE_HEIGHT, PAGE_WIDTH } from '@/lib/paper';

// The real module minus its `api`: `ApiError` and `NetworkError` are the two
// classes the offline queue decides with (`instanceof`), so stubbing them away
// would quietly change which failures are treated as terminal.
vi.mock('../../hooks/api', async () => {
  const actual =
    await vi.importActual<typeof import('../../hooks/api')>('../../hooks/api');
  return {
    ...actual,
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
  };
});

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

describe('a picture pasted with no backend in reach', () => {
  /** A paste event carrying one image file, the shape the handler reads. */
  const pasteImage = (file: File) => {
    const event = new Event('paste') as Event & { clipboardData: unknown };
    event.clipboardData = {
      items: [{ kind: 'file', type: file.type, getAsFile: () => file }],
    };
    window.dispatchEvent(event);
  };

  it('lands on the page, on the device, and uploads under the id it already has', async () => {
    // The one thing on a paper page that cannot be redrawn — and paper only
    // ever exists on the tablet it was written on, which may be nowhere near
    // the server. So: on the page at once, in IndexedDB at once, uploaded
    // whenever the backend comes back, under the id the page already shows.
    const created = vi.fn().mockResolvedValue({ id: 'ignored' });
    vi.mocked(api.paper.addImage).mockImplementation(created);
    const objectUrls: Blob[] = [];
    URL.createObjectURL = vi.fn((b: Blob) => {
      objectUrls.push(b);
      return 'blob:pasted';
    }) as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = vi.fn();
    // naturalSize resolves off an <img> load, which jsdom never fires.
    Object.defineProperty(HTMLImageElement.prototype, 'src', {
      configurable: true,
      set() {
        setTimeout(() => this.onerror?.(), 0);
      },
    });

    const { queryClient } = renderEditor();
    await waitFor(() => expect(api.paper.getPage).toHaveBeenCalled());

    onlineManager.setOnline(false);
    const file = new File(['pixels'], 'pasted.png', { type: 'image/png' });
    await act(async () => {
      pasteImage(file);
      await new Promise(r => setTimeout(r, 10));
    });

    // On the page immediately, carrying no server url — the editor draws it
    // from the device instead, which is what survives a reload.
    const page = queryClient.getQueryData<{
      images: { id: string; url: string }[];
    }>(['paper', 'page', PAGE_1]);
    expect(page?.images).toHaveLength(1);
    const imageId = page!.images[0].id;
    expect(page!.images[0].url).toBe('');

    // And on the device: the bytes are in the store under that same id, so the
    // upload can happen an hour from now from the same picture.
    const { getPhoto } = await import('@/offline/photoStore');
    const stored = await getPhoto(imageId);
    expect(await stored!.blob.text()).toBe('pixels');
    expect(stored!.meta.placement).toBeTruthy();

    // Nothing was sent — there was nowhere to send it.
    expect(api.paper.addImage).not.toHaveBeenCalled();

    // …and when the backend comes back it goes up under that same id, so the
    // replay cannot paste the picture a second time.
    onlineManager.setOnline(true);
    await act(() => queryClient.resumePausedMutations());
    await waitFor(() => expect(api.paper.addImage).toHaveBeenCalled());
    expect(vi.mocked(api.paper.addImage).mock.calls[0][4]).toBe(imageId);
    await waitFor(async () => expect(await getPhoto(imageId)).toBeUndefined());
  });

  it("re-renders the page's snapshot, so a page that is only a photo is not a blank thumbnail", async () => {
    // The snapshot is what the explorer grid and the Journal filmstrip show,
    // and it is only regenerated when the page is dirty. Adding a picture never
    // marked it so: a page whose only content was a photo saved a blank sheet
    // and stayed blank until something was drawn on it.
    vi.mocked(api.paper.addImage).mockResolvedValue({ id: 'ignored' } as never);
    URL.createObjectURL = vi.fn(
      () => 'blob:pasted'
    ) as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = vi.fn();
    Object.defineProperty(HTMLImageElement.prototype, 'src', {
      configurable: true,
      set() {
        setTimeout(() => this.onerror?.(), 0);
      },
    });

    renderEditor();
    await waitFor(() => expect(api.paper.getPage).toHaveBeenCalled());

    await act(async () => {
      pasteImage(new File(['pixels'], 'pasted.png', { type: 'image/png' }));
      await new Promise(r => setTimeout(r, 10));
    });

    const save = screen.getByRole('button', { name: /save/i });
    expect((save as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(api.paper.savePage).toHaveBeenCalled());
    expect(
      vi.mocked(api.paper.savePage).mock.calls[0][1].snapshot
    ).toBeTruthy();
  });

  it('says so when the server refuses it, and sends it again on demand', async () => {
    // What this pins: every picture put on a page from the iPad was answered
    // 400 (the filename said .HEIC and the server had never heard of it) and
    // nothing said a word. The paste kept showing — it was being drawn from the
    // blob it came from — so the loss only surfaced on the way back to the
    // page, by which time the picture was simply gone.
    vi.mocked(api.paper.addImage).mockRejectedValue(
      new ApiError('unsupported image type: image/heic', 400)
    );
    URL.createObjectURL = vi.fn(
      () => 'blob:pasted'
    ) as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = vi.fn();
    Object.defineProperty(HTMLImageElement.prototype, 'src', {
      configurable: true,
      set() {
        setTimeout(() => this.onerror?.(), 0);
      },
    });

    renderEditor();
    await waitFor(() => expect(api.paper.getPage).toHaveBeenCalled());

    const file = new File(['pixels'], 'IMG_0042.HEIC', { type: 'image/heic' });
    await act(async () => {
      pasteImage(file);
      await new Promise(r => setTimeout(r, 20));
    });

    // Visible, naming the reason, and offering the way out.
    await screen.findByText(/never reached the server/);
    expect(screen.getByText(/unsupported image type/)).toBeTruthy();

    // And the bytes are still here — that is what makes the retry worth
    // offering at all.
    const { listPhotos } = await import('@/offline/photoStore');
    const [held] = (await listPhotos()).filter(p => p.target === 'paper');
    expect(held.failed).toBe(true);

    // A backend that has learned to accept the format: the same picture goes
    // up, under the id it already had.
    vi.mocked(api.paper.addImage).mockResolvedValue({
      id: held.id,
      pageId: PAGE_1,
      url: `/api/paper/images/${held.id}/file?v=1`,
      x: held.placement!.x,
      y: held.placement!.y,
      width: held.placement!.width,
      height: held.placement!.height,
      rotation: 0,
      flipped: 0,
      locked: 0,
      position: 0,
    } as never);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /retry pictures/i }));
      await new Promise(r => setTimeout(r, 20));
    });

    await waitFor(() => expect(api.paper.addImage).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.paper.addImage).mock.calls[1][4]).toBe(held.id);
    await waitFor(() =>
      expect(screen.queryByText(/never reached the server/)).toBeNull()
    );
  });
});
