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
import { registerOfflineMutationDefaults } from '@/offline/mutationDefaults';
import { ImmersiveProvider, useImmersive } from '@/components/ImmersiveContext';
import { fitPageBox, PAGE_HEIGHT, PAGE_WIDTH } from '@/lib/paper';

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

// The device stores fall back to a module-level Map under jsdom (there is no
// IndexedDB), and that Map outlives a test. Empty it, or one test's held photo
// counts as another test's unsaved page.
beforeEach(async () => {
  const { listPhotos, deletePhoto } = await import('@/offline/photoStore');
  for (const photo of await listPhotos()) await deletePhoto(photo.id);
  const { listPageSaves, clearPageSave } = await import('@/offline/pageStore');
  for (const save of await listPageSaves()) {
    await clearPageSave(save.pageId, save.revision);
  }
});

/** Draw one stroke on the mounted canvas, and hand it back. */
async function drawOn(container: HTMLElement) {
  const canvas = await waitFor(() => {
    const c = container.querySelector('canvas');
    expect(c).toBeTruthy();
    return c!;
  });
  canvas.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 400, height: 566 }) as DOMRect;
  await act(async () => {
    canvas.dispatchEvent(
      new MouseEvent('pointerdown', { bubbles: true, clientX: 20, clientY: 20 })
    );
    canvas.dispatchEvent(
      new MouseEvent('pointermove', {
        bubbles: true,
        clientX: 80,
        clientY: 120,
      })
    );
    canvas.dispatchEvent(
      new MouseEvent('pointerup', { bubbles: true, clientX: 80, clientY: 120 })
    );
  });
  return canvas;
}

function renderEditor() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 1000 * 60 } },
  });
  // Paper's writes are enqueued by key, not driven by a hook (Save fires a
  // burst of them), so the editor only works against a client that carries the
  // registered config — exactly as main.tsx sets it up.
  registerOfflineMutationDefaults(queryClient);
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

    // Nothing was sent — and nothing would have been sent with the wifi on
    // either. A paper reaches the server when Save is pressed and at no other
    // moment, pictures included.
    expect(api.paper.addImage).not.toHaveBeenCalled();

    // Save, still with nowhere to send it: the upload queues rather than fails.
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save/i }));
      await new Promise(r => setTimeout(r, 10));
    });
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

    // The refusal can only happen on the way out, which is now Save.
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save/i }));
      await new Promise(r => setTimeout(r, 20));
    });

    // Visible, naming the reason, and offering the way out.
    await screen.findByText(/never reached the server/);
    expect(screen.getByText(/unsupported image type/)).toBeTruthy();

    // And marked on the picture itself. The banner is about the paper, which
    // is every page of it; a paper with four pictures gave no clue which one
    // was still stuck on the device — a refused picture draws exactly like a
    // saved one, from the blob it was pasted from.
    const marked = await screen.findByRole('img', {
      name: /never reached the server/,
    });
    expect(marked.getAttribute('title')).toContain('unsupported image type');

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
    // The mark goes with it — the picture is on the server now.
    expect(
      screen.queryByRole('img', { name: /never reached the server/ })
    ).toBeNull();
  });
});

describe('leaving a page never reaches the server', () => {
  // What this pins: drawing on an iPad with bad wifi. Flipping a page used to
  // upload it, so every page turn waited on a request that might never land —
  // and the fix is not a faster upload, it is not uploading at all. The page is
  // written to the device; the server hears about it when Save is pressed.
  it('flipping, adding a page, backing out and app-switching only store locally', async () => {
    const { container } = renderEditor();
    await drawOn(container);

    const { getPageSave } = await import('@/offline/pageStore');

    await act(async () => {
      fireEvent.click(screen.getByTitle('Next page'));
    });
    expect(api.paper.savePage).not.toHaveBeenCalled();
    // …but the page is safe: the whole payload, snapshot included, is here.
    const stored = await getPageSave(PAGE_1);
    expect(stored?.meta.strokes).not.toBe('[]');
    expect(stored?.snapshot).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByTitle('Add a new page at the end'));
    });
    const setVisibility = (value: string) => {
      Object.defineProperty(document, 'visibilityState', {
        configurable: true,
        value,
      });
      document.dispatchEvent(new Event('visibilitychange'));
    };
    await act(async () => {
      setVisibility('hidden');
      await new Promise(r => setTimeout(r, 10));
    });
    // Put it back: a document left hidden is a global, and react-query's
    // retryer reads it — the next test's uploads would sit paused forever.
    await act(async () => {
      setVisibility('visible');
      await new Promise(r => setTimeout(r, 10));
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /back/i }));
    });

    expect(api.paper.savePage).not.toHaveBeenCalled();
  });
});

describe('Save sends the whole paper', () => {
  it('uploads every page the device is holding, not just the one on screen', async () => {
    // A paper is one thing to the person drawing it. Four pages of the same
    // notebook, one press of Save — including the pages already flipped away
    // from, whose canvases are long gone.
    const { container } = renderEditor();
    await drawOn(container);

    await act(async () => {
      fireEvent.click(screen.getByTitle('Next page'));
    });
    await drawOn(container);

    // Both pages are waiting, and the status says so rather than "Saved".
    await waitFor(() => expect(screen.getByText('2 unsaved')).toBeTruthy());

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save/i }));
    });

    await waitFor(() => expect(api.paper.savePage).toHaveBeenCalledTimes(2));
    const uploaded = vi
      .mocked(api.paper.savePage)
      .mock.calls.map(c => c[0])
      .sort();
    expect(uploaded).toEqual([PAGE_1, PAGE_2]);
    // And the page that is not on screen carried its own ink, not a blank.
    for (const call of vi.mocked(api.paper.savePage).mock.calls) {
      expect(call[1].strokes).not.toBe('[]');
    }
  });
});

describe('a picture pasted, moved, then saved before it ever uploaded', () => {
  // What this pins: paste a picture, drag it off centre, keep writing, press
  // Save. The picture is still only a blob on this device at that point — its
  // add hasn't gone out yet — so its move lived in `staged.edits` under the
  // same id as its pending upload. Sending that as a PATCH first 404s (no row
  // to patch), and the add that followed then created the row back at the
  // *original* pasted position, undoing the move the instant Save finished.
  it('uploads at the moved position instead of the original pasted one', async () => {
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
    vi.mocked(api.paper.addImage).mockResolvedValue({ id: 'ignored' } as never);

    const { container, queryClient } = renderEditor();
    await waitFor(() => expect(container.querySelector('canvas')).toBeTruthy());

    await act(async () => {
      const event = new Event('paste') as Event & { clipboardData: unknown };
      event.clipboardData = {
        items: [
          {
            kind: 'file',
            type: 'image/png',
            getAsFile: () =>
              new File(['pixels'], 'pasted.png', { type: 'image/png' }),
          },
        ],
      };
      window.dispatchEvent(event);
      await new Promise(r => setTimeout(r, 10));
    });

    const pasted = queryClient.getQueryData<{
      images: {
        id: string;
        x: number;
        y: number;
        width: number;
        height: number;
      }[];
    }>(['paper', 'page', PAGE_1])!.images[0];
    const originalX = pasted.x;

    // The layer converts client coordinates to page space through its own
    // (jsdom-default, all-zero) bounding rect and the same scale PaperEditor
    // computes from the 800x1000 stage the beforeEach block sets up — so a
    // pointer at the pasted picture's own centre, in that scale, actually
    // lands on it instead of missing and just deselecting.
    const box = fitPageBox({ width: 800 - 20, height: 1000 - 20 });
    const scale = box.width / PAGE_WIDTH;
    const centerClientX = (pasted.x + pasted.width / 2) * scale;
    const centerClientY = (pasted.y + pasted.height / 2) * scale;

    // Move it — select mode is already on, and the paste already chose it.
    const layer = await waitFor(() => screen.getByTestId('paper-image-layer'));
    fireEvent.pointerDown(layer, {
      clientX: centerClientX,
      clientY: centerClientY,
      pointerId: 1,
    });
    fireEvent.pointerMove(layer, {
      clientX: centerClientX + 150,
      clientY: centerClientY,
      pointerId: 1,
    });
    fireEvent.pointerUp(layer, {
      clientX: centerClientX + 150,
      clientY: centerClientY,
      pointerId: 1,
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save/i }));
      await new Promise(r => setTimeout(r, 10));
    });

    await waitFor(() => expect(api.paper.addImage).toHaveBeenCalled());
    const [, , uploadedBox] = vi.mocked(api.paper.addImage).mock.calls[0];
    // The box the upload actually carried is the moved one, not the original.
    expect(uploadedBox.x).not.toBe(originalX);
    // And nothing tried to PATCH a row that didn't exist yet.
    expect(api.paper.updateImage).not.toHaveBeenCalled();
  });
});

describe('a picture whose upload never landed', () => {
  /** Paste one picture onto the open page and hand back its cached row. */
  const pasteOne = async (queryClient: QueryClient) => {
    await act(async () => {
      const event = new Event('paste') as Event & { clipboardData: unknown };
      event.clipboardData = {
        items: [
          {
            kind: 'file',
            type: 'image/png',
            getAsFile: () =>
              new File(['pixels'], 'pasted.png', { type: 'image/png' }),
          },
        ],
      };
      window.dispatchEvent(event);
      await new Promise(r => setTimeout(r, 10));
    });
    return queryClient.getQueryData<{
      images: {
        id: string;
        x: number;
        y: number;
        width: number;
        height: number;
      }[];
    }>(['paper', 'page', PAGE_1])!.images[0];
  };

  /** Drag the picture `dx` page-units to the right, through the overlay. */
  const dragRight = async (
    picture: { x: number; y: number; width: number; height: number },
    dx: number
  ) => {
    const box = fitPageBox({ width: 800 - 20, height: 1000 - 20 });
    const scale = box.width / PAGE_WIDTH;
    const cx = (picture.x + picture.width / 2) * scale;
    const cy = (picture.y + picture.height / 2) * scale;
    const layer = await waitFor(() => screen.getByTestId('paper-image-layer'));
    fireEvent.pointerDown(layer, { clientX: cx, clientY: cy, pointerId: 1 });
    fireEvent.pointerMove(layer, {
      clientX: cx + dx * scale,
      clientY: cy,
      pointerId: 1,
    });
    fireEvent.pointerUp(layer, {
      clientX: cx + dx * scale,
      clientY: cy,
      pointerId: 1,
    });
  };

  const save = async () => {
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save/i }));
      await new Promise(r => setTimeout(r, 20));
    });
  };

  it('goes up at the moved box on the next Save, not back in the middle', async () => {
    // The morning: paste a screenshot, resize it, write around it, Save. The
    // upload failed, and Save clears the staged edits as it enqueues the writes
    // — landed or not — so the only surviving record of where the picture sat
    // was `placement`, written once at paste time. The evening's Save duly
    // re-sent the picture into the middle of the page.
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
    // A 500 is not terminal: the picture stays queueable, which is exactly the
    // case where its box has to survive to the next attempt.
    vi.mocked(api.paper.addImage).mockRejectedValue(
      new ApiError('server exploded', 500)
    );

    const { container, queryClient } = renderEditor();
    await waitFor(() => expect(container.querySelector('canvas')).toBeTruthy());

    const pasted = await pasteOne(queryClient);
    await dragRight(pasted, 150);
    await save();

    await waitFor(() => expect(api.paper.addImage).toHaveBeenCalledTimes(1));
    const movedX = vi.mocked(api.paper.addImage).mock.calls[0][2].x;
    expect(movedX).toBeGreaterThan(pasted.x);

    // The device is still holding it, and holding it where it actually sits.
    const { listPhotos } = await import('@/offline/photoStore');
    const [held] = (await listPhotos()).filter(p => p.target === 'paper');
    expect(held.failed).toBe(false);
    expect(held.placement!.x).toBeCloseTo(movedX, 5);

    // Press Save again — the whole point of a queued picture. It goes up where
    // it was left, not where it was pasted.
    vi.mocked(api.paper.addImage).mockResolvedValue({ id: 'ignored' } as never);
    await save();

    await waitFor(() => expect(api.paper.addImage).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.paper.addImage).mock.calls[1][2].x).toBeCloseTo(
      movedX,
      5
    );
  });

  it('stays where it was put on the page while its upload is still pending', async () => {
    // Save clears the staged edits as it fires the writes, so the moved box has
    // to be on the cached row by then or the picture visibly snaps back to the
    // middle the instant Save is pressed — and stays there until the upload
    // lands, which for a paused one can be the next day.
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

    const { container, queryClient } = renderEditor();
    await waitFor(() => expect(container.querySelector('canvas')).toBeTruthy());

    const pasted = await pasteOne(queryClient);
    await dragRight(pasted, 150);

    onlineManager.setOnline(false);
    await save();
    onlineManager.setOnline(true);

    const row = queryClient.getQueryData<{
      images: { id: string; x: number; url: string }[];
    }>(['paper', 'page', PAGE_1])!.images[0];
    expect(row.url).toBe('');
    expect(row.x).toBeGreaterThan(pasted.x);
  });
});

describe('a page opened while the device still holds a picture for it', () => {
  it('puts the picture back on the page instead of losing it', async () => {
    // What this pins: a picture that has not been uploaded is on its page as an
    // optimistic row in the query cache and nowhere else — the boot sweep
    // leaves paper alone on purpose, since nothing in a paper reaches the
    // server except by pressing Save. So a cache entry that did not survive to
    // the next session (a persist that failed, a PERSIST_BUSTER bump) took the
    // picture off the page with it, while the bytes and the box sat in
    // IndexedDB. The page came back without it, and the next Save uploaded it
    // as though it were new — in the middle of the page.
    URL.createObjectURL = vi.fn(
      () => 'blob:held'
    ) as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = vi.fn();

    const { storePhoto } = await import('@/offline/photoStore');
    const placement = { x: 80, y: 120, width: 600, height: 891 };
    await storePhoto(
      'held-1',
      new File(['pixels'], 'pasted.png', { type: 'image/png' }),
      'paper',
      PAGE_1,
      placement
    );

    // The server has never heard of it, which is the whole situation.
    vi.mocked(api.paper.getPage).mockResolvedValue({
      strokes: '[]',
      width: null,
      height: null,
      images: [],
    });

    const { queryClient } = renderEditor();
    await waitFor(() => expect(api.paper.getPage).toHaveBeenCalled());

    const row = await waitFor(() => {
      const images = queryClient.getQueryData<{
        images: {
          id: string;
          url: string;
          x: number;
          y: number;
          width: number;
          height: number;
        }[];
      }>(['paper', 'page', PAGE_1])!.images;
      expect(images).toHaveLength(1);
      return images[0];
    });
    // Back where it was left, with no server url — it is still this device's.
    expect(row.id).toBe('held-1');
    expect(row.url).toBe('');
    expect({
      x: row.x,
      y: row.y,
      width: row.width,
      height: row.height,
    }).toEqual(placement);

    // And the page counts as unsaved because of it, so Save can send it.
    expect(
      (screen.getByRole('button', { name: /save/i }) as HTMLButtonElement)
        .disabled
    ).toBe(false);
  });

  it('leaves a picture the server already has alone', async () => {
    // The device copy is deleted the moment an upload is confirmed, so there is
    // normally nothing to re-place; a stale one must never overwrite the row
    // the server just answered with.
    URL.createObjectURL = vi.fn(
      () => 'blob:held'
    ) as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = vi.fn();

    const { storePhoto } = await import('@/offline/photoStore');
    await storePhoto(
      'img-1',
      new File(['pixels'], 'pasted.png', { type: 'image/png' }),
      'paper',
      PAGE_1,
      { x: 0, y: 0, width: 100, height: 100 }
    );
    vi.mocked(api.paper.getPage).mockResolvedValue({
      strokes: '[]',
      width: null,
      height: null,
      images: [
        {
          id: 'img-1',
          pageId: PAGE_1,
          url: '/api/paper/images/img-1/file?v=1',
          x: 700,
          y: 800,
          width: 500,
          height: 500,
          rotation: 0,
          flipped: 0,
          locked: 0,
          position: 0,
        },
      ],
    } as never);

    const { queryClient } = renderEditor();
    await waitFor(() => expect(api.paper.getPage).toHaveBeenCalled());
    await act(async () => {
      await new Promise(r => setTimeout(r, 20));
    });

    const images = queryClient.getQueryData<{
      images: { id: string; url: string; x: number }[];
    }>(['paper', 'page', PAGE_1])!.images;
    expect(images).toHaveLength(1);
    expect(images[0].x).toBe(700);
    expect(images[0].url).toContain('/api/paper/images/img-1/file');
  });
});

describe('rotating a picture before it ever uploaded', () => {
  it('creates the row first, then patches the rotation onto it', async () => {
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
    vi.mocked(api.paper.addImage).mockResolvedValue({ id: 'ignored' } as never);

    const { container } = renderEditor();
    await waitFor(() => expect(container.querySelector('canvas')).toBeTruthy());

    await act(async () => {
      const event = new Event('paste') as Event & { clipboardData: unknown };
      event.clipboardData = {
        items: [
          {
            kind: 'file',
            type: 'image/png',
            getAsFile: () =>
              new File(['pixels'], 'pasted.png', { type: 'image/png' }),
          },
        ],
      };
      window.dispatchEvent(event);
      await new Promise(r => setTimeout(r, 10));
    });

    // Already selected by the paste — rotate it without ever having saved.
    fireEvent.click(await screen.findByLabelText('Rotate right 90°'));

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save/i }));
      await new Promise(r => setTimeout(r, 10));
    });

    await waitFor(() => expect(api.paper.addImage).toHaveBeenCalled());
    await waitFor(() => expect(api.paper.updateImage).toHaveBeenCalled());
    // Order matters: the row has to exist before the PATCH can land on it.
    const addOrder = vi.mocked(api.paper.addImage).mock.invocationCallOrder[0];
    const updateOrder = vi.mocked(api.paper.updateImage).mock
      .invocationCallOrder[0];
    expect(addOrder).toBeLessThan(updateOrder);
    expect(vi.mocked(api.paper.updateImage).mock.calls[0][1]).toEqual({
      rotation: 90,
    });
  });
});

describe('the images prop stays stable while drawing', () => {
  // What this pins: a local commit (the idle timer, or leaving the page)
  // rewrites the page's query cache to carry the freshly drawn strokes —
  // that's the whole point of it being local-only. But it used to also produce
  // a brand-new `images` array on every one of those writes even though the
  // pictures themselves hadn't changed, and PaperCanvas redraws itself from
  // scratch whenever its `images` prop changes identity — wiping an
  // in-progress, not-yet-committed stroke until it landed. A committed
  // stroke's own repaint painted over the gap, which is what read as "some of
  // what I wrote erased and came back."
  it('keeps the same images array across a local commit that only touched strokes', async () => {
    vi.mocked(api.paper.getPage).mockResolvedValue({
      strokes: '[]',
      width: null,
      height: null,
      images: [
        {
          id: 'img-1',
          url: '/api/paper/images/img-1/file',
          x: 100,
          y: 100,
          width: 200,
          height: 200,
          rotation: 0,
          flipped: 0,
          locked: 0,
          position: 0,
        },
      ],
    } as never);

    const { container, queryClient } = renderEditor();
    const canvas = await drawOn(container);
    const before = queryClient.getQueryData<{ images: unknown }>([
      'paper',
      'page',
      PAGE_1,
    ])!.images;

    // A second stroke stands in for the idle-timer's local commit — both take
    // the same commitLocal path, and this way the test needs no fake timers.
    await act(async () => {
      canvas.dispatchEvent(
        new MouseEvent('pointerdown', {
          bubbles: true,
          clientX: 30,
          clientY: 30,
        })
      );
      canvas.dispatchEvent(
        new MouseEvent('pointerup', { bubbles: true, clientX: 30, clientY: 30 })
      );
    });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(api.paper.savePage).toHaveBeenCalled());

    const after = queryClient.getQueryData<{ images: unknown }>([
      'paper',
      'page',
      PAGE_1,
    ])!.images;
    // Same array, not merely equal content — that identity is what keeps
    // PaperCanvas's images-effect from firing on a strokes-only commit.
    expect(after).toBe(before);
  });
});

describe('the page is never refetched while it is already cached', () => {
  it('keeps a not-yet-uploaded picture across a page flip past the stale time', async () => {
    // The invariant: this client is the only writer to a page's content, so the
    // server is only ever *behind* the cache, never ahead. A GET against a
    // populated cache can return equal-or-older content and nothing else.
    //
    // What made that matter rather than merely wasteful: Save is manual, so a
    // page sits legitimately ahead of the server for as long as the session
    // lasts, and a picture pasted in that window exists *only* as an optimistic
    // row in the cache — the server has never heard of it. React Query replaces
    // query data wholesale on a successful fetch, so one ordinary background
    // refetch (60s idle, then an app-switch) would drop that row and the
    // picture would disappear off the page.
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

    const { container, queryClient } = renderEditor();
    await waitFor(() => expect(api.paper.getPage).toHaveBeenCalledWith(PAGE_1));

    await act(async () => {
      const event = new Event('paste') as Event & { clipboardData: unknown };
      event.clipboardData = {
        items: [
          {
            kind: 'file',
            type: 'image/png',
            getAsFile: () =>
              new File(['pixels'], 'pasted.png', { type: 'image/png' }),
          },
        ],
      };
      window.dispatchEvent(event);
      await new Promise(r => setTimeout(r, 10));
    });
    expect(
      queryClient.getQueryData<{ images: unknown[] }>(['paper', 'page', PAGE_1])
        ?.images
    ).toHaveLength(1);

    const fetchesOfPage1 = () =>
      vi.mocked(api.paper.getPage).mock.calls.filter(c => c[0] === PAGE_1)
        .length;
    const before = fetchesOfPage1();

    // Flip away first, at the real clock: leaving the page commits it locally,
    // and that write refreshes the cache entry's timestamp. Jumping the clock
    // before this point would have made the entry look freshly written and the
    // test would pass without proving anything.
    await act(async () => {
      fireEvent.click(screen.getByTitle('Next page'));
      await new Promise(r => setTimeout(r, 10));
    });

    // Now jump well past the 60s staleTime the app configures, so page 1's
    // entry is firmly stale, and flip back — the page-content query gains an
    // observer again, which is exactly when refetchOnMount fires. Date.now is
    // what react-query measures staleness with; spying on it beats fake timers,
    // which would deadlock the awaits in this file.
    const realNow = Date.now();
    const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(realNow + 120_000);
    try {
      await act(async () => {
        fireEvent.click(screen.getByTitle('Previous page'));
        await new Promise(r => setTimeout(r, 20));
      });
    } finally {
      nowSpy.mockRestore();
    }

    await waitFor(() => expect(container.querySelector('canvas')).toBeTruthy());
    expect(fetchesOfPage1()).toBe(before);
    // And the picture the server has never heard of is still on the page.
    expect(
      queryClient.getQueryData<{ images: unknown[] }>(['paper', 'page', PAGE_1])
        ?.images
    ).toHaveLength(1);
  });
});

describe('deleting a picture', () => {
  const serverImage = {
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
  };

  it('stages the delete and only sends it on Save', async () => {
    vi.mocked(api.paper.getPage).mockResolvedValue({
      strokes: '[]',
      width: null,
      height: null,
      images: [serverImage],
    } as never);
    vi.mocked(api.paper.deleteImage).mockResolvedValue({
      success: true,
    } as never);

    const { container } = renderEditor();
    await waitFor(() => expect(container.querySelector('canvas')).toBeTruthy());

    fireEvent.click(
      screen.getByTitle('Select and move pictures instead of drawing')
    );
    const layer = await waitFor(() => screen.getByTestId('paper-image-layer'));
    fireEvent.pointerDown(layer, { clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerUp(layer, { clientX: 100, clientY: 100, pointerId: 1 });

    await act(async () => {
      fireEvent.click(screen.getByLabelText('Delete image'));
    });

    // Off the page at once — a staged delete the user cannot see is a delete
    // they will press twice — and not a word to the server yet.
    expect(api.paper.deleteImage).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.queryByTestId('paper-image-actions')).toBeNull()
    );

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save/i }));
    });
    await waitFor(() =>
      expect(api.paper.deleteImage).toHaveBeenCalledWith('img-1')
    );
  });

  it('drops a picture that never reached the server without asking it', async () => {
    // There is no row for a DELETE to find: the picture is a blob this device
    // is holding. Sending one would 404, and keeping the bytes would leak them.
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

    const { container } = renderEditor();
    await waitFor(() => expect(container.querySelector('canvas')).toBeTruthy());

    await act(async () => {
      const event = new Event('paste') as Event & { clipboardData: unknown };
      event.clipboardData = {
        items: [
          {
            kind: 'file',
            type: 'image/png',
            getAsFile: () =>
              new File(['pixels'], 'pasted.png', { type: 'image/png' }),
          },
        ],
      };
      window.dispatchEvent(event);
      await new Promise(r => setTimeout(r, 10));
    });

    const { listPhotos } = await import('@/offline/photoStore');
    expect((await listPhotos()).filter(p => p.target === 'paper')).toHaveLength(
      1
    );

    // Pasting lands in select mode with the new picture chosen.
    await act(async () => {
      fireEvent.click(await screen.findByLabelText('Delete image'));
    });

    expect(api.paper.deleteImage).not.toHaveBeenCalled();
    await waitFor(async () =>
      expect(
        (await listPhotos()).filter(p => p.target === 'paper')
      ).toHaveLength(0)
    );

    // And nothing is left waiting to be sent for it.
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save/i }));
    });
    expect(api.paper.addImage).not.toHaveBeenCalled();
  });
});

describe('the Paste button', () => {
  // On an iPad there is no Cmd+V and press-and-hold offers to select text that
  // is not there, so the window 'paste' listener is unreachable — a picture
  // could not be pasted onto a page at all. This button is the way in.
  const withClipboard = (read: () => Promise<unknown[]>) => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { read },
    });
  };

  beforeEach(() => {
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
  });

  it('puts the clipboard image on the page, on the device', async () => {
    withClipboard(async () => [
      {
        types: ['text/plain', 'image/png'],
        getType: async () => new Blob(['pixels'], { type: 'image/png' }),
      },
    ]);

    const { container, queryClient } = renderEditor();
    await waitFor(() => expect(container.querySelector('canvas')).toBeTruthy());

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /paste/i }));
      await new Promise(r => setTimeout(r, 10));
    });

    const page = queryClient.getQueryData<{ images: unknown[] }>([
      'paper',
      'page',
      PAGE_1,
    ]);
    expect(page?.images).toHaveLength(1);
    const { listPhotos } = await import('@/offline/photoStore');
    expect((await listPhotos()).filter(p => p.target === 'paper')).toHaveLength(
      1
    );
    // Still nothing sent: the button pastes, Save uploads.
    expect(api.paper.addImage).not.toHaveBeenCalled();
  });

  it('says so when there is no picture on the clipboard', async () => {
    withClipboard(async () => [
      { types: ['text/plain'], getType: async () => new Blob(['hi']) },
    ]);

    const { container } = renderEditor();
    await waitFor(() => expect(container.querySelector('canvas')).toBeTruthy());

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /paste/i }));
      await new Promise(r => setTimeout(r, 10));
    });

    expect(screen.getByText(/no picture on the clipboard/)).toBeTruthy();
    const { listPhotos } = await import('@/offline/photoStore');
    expect((await listPhotos()).filter(p => p.target === 'paper')).toHaveLength(
      0
    );
  });

  it('points at the file button when the browser refuses the clipboard', async () => {
    // Safari can decline the read outright, and a dead button that says nothing
    // is indistinguishable from a broken app.
    withClipboard(async () => {
      throw new Error('NotAllowedError');
    });

    const { container } = renderEditor();
    await waitFor(() => expect(container.querySelector('canvas')).toBeTruthy());

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /paste/i }));
      await new Promise(r => setTimeout(r, 10));
    });

    expect(screen.getByText(/use 🖼 Add instead/)).toBeTruthy();
  });
});

describe('the app chrome while a page is open', () => {
  // On an iPad the page is the screen. The ☰ header, the sidebar rail and the
  // Transcribe/Journal/Record bar are wasted height beside an A4 sheet and a
  // row of tap targets a resting palm can hit; the toolbar's Back button is the
  // way out. On a desktop with a mouse there is room for all of it.
  function Probe() {
    return <div>{useImmersive() ? 'chrome hidden' : 'chrome shown'}</div>;
  }

  function renderWithChrome() {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 1000 * 60 } },
    });
    registerOfflineMutationDefaults(queryClient);
    return render(
      <QueryClientProvider client={queryClient}>
        <ImmersiveProvider>
          <Probe />
          <PaperEditor paperId="doc-1" onBack={() => {}} />
        </ImmersiveProvider>
      </QueryClientProvider>
    );
  }

  const pointer = (kind: 'coarse' | 'fine') => {
    window.matchMedia = ((query: string) => ({
      matches: query.includes(kind),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      onchange: null,
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  };

  it('gets out of the way on a touch screen, and comes back on unmount', async () => {
    pointer('coarse');
    const { unmount } = renderWithChrome();
    await waitFor(() => expect(screen.getByText('chrome hidden')).toBeTruthy());
    unmount();
  });

  it('leaves a desktop alone', async () => {
    pointer('fine');
    const { container } = renderWithChrome();
    await waitFor(() => expect(container.querySelector('canvas')).toBeTruthy());
    expect(screen.getByText('chrome shown')).toBeTruthy();
  });
});
