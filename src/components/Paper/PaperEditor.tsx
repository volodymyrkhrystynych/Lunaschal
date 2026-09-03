import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { usePaperPageAdd, MUTATION_KEYS } from '@/offline/mutationDefaults';
import { getPageSave, listPageSaves, storePageSave } from '@/offline/pageStore';
import {
  clearFailure,
  deletePhoto,
  getPhoto,
  listPhotos,
  storePhoto,
  type StoredPhoto,
} from '@/offline/photoStore';
import {
  enqueuePaperWrite,
  insertPendingPaperImage,
} from '@/offline/mutationDefaults';
import { useImmersiveView } from '@/components/ImmersiveContext';
import { isTouchDevice } from '@/lib/deviceInput';
import { ulid } from '@/lib/ulid';
import { get as idbGet, set as idbSet, del as idbDel } from 'idb-keyval';
import { api, type PaperPageContent } from '../../hooks/api';
import {
  fitPageBox,
  PAGE_WIDTH,
  parseStrokes,
  resolveSwipe,
  saveStatusLabel,
  TOOL_SIZES,
  type StrokeTool,
  type SwipeDirection,
} from '../../lib/paper';
import {
  fitPastedImage,
  pastedFilename,
  rotateBy,
  snapRotation,
  type ImageBox,
  type PageImage,
} from '../../lib/paperImages';
import { PaperCanvas, type PaperCanvasHandle } from './PaperCanvas';
import { PaperToolPanel } from './PaperToolPanel';
import { PaperImageLayer } from './PaperImageLayer';
import { PaperImageActions } from './PaperImageActions';

/** Breathing room between the fitted page and the edge of the drawing area, so
 * the panel has somewhere to sit when it is docked. */
const PAGE_INSET = 10;

/** How long the pen has to be still before the page is written to the device.
 * Local only — two IndexedDB writes and a canvas snapshot, no request — so it
 * can afford to be short. Nothing here ever talks to the server on a timer. */
const LOCAL_COMMIT_DELAY_MS = 2000;

type PendingImageEdit = Partial<
  ImageBox & Pick<PageImage, 'rotation' | 'flipped' | 'locked'>
>;

const imageEditsBufferKey = (pageId: string) => `paper-page-images-${pageId}`;
const imageDeletesBufferKey = (pageId: string) =>
  `paper-page-image-deletes-${pageId}`;

/** Parse a buffered set of staged picture edits, defensively — a corrupt or
 * missing entry just means nothing is pending, never a crash. */
function parseImageEditsBuffer(raw: unknown): Record<string, PendingImageEdit> {
  if (typeof raw !== 'string' || !raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

/** Parse a buffered set of staged picture deletions, as defensively as the
 * edits above: anything unreadable means nothing is staged. */
function parseImageDeletesBuffer(raw: unknown): string[] {
  if (typeof raw !== 'string' || !raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((v): v is string => typeof v === 'string')
      : [];
  } catch {
    return [];
  }
}

/** Everything staged for one page that isn't ink. Carried together with the
 * page id they belong to because the page can change under them: the buffer
 * they are mirrored into is keyed on *this* id, not on whatever page happens to
 * be on screen when the write lands. */
interface StagedImages {
  pageId: string | null;
  edits: Record<string, PendingImageEdit>;
  deletes: string[];
}

const NO_STAGED_IMAGES: StagedImages = {
  pageId: null,
  edits: {},
  deletes: [],
};

interface PaperEditorProps {
  paperId: string;
  onBack: () => void;
}

export function PaperEditor({ paperId, onBack }: PaperEditorProps) {
  // On a tablet the page *is* the screen: no ☰ header, no sidebar rail, no
  // Transcribe/Journal/Record bar along the bottom. Those are wasted height
  // beside an A4 page and a row of tap targets a resting palm can hit, and the
  // toolbar's own Back button is the way out. Gated on a coarse primary pointer
  // rather than a width: an iPad is wider than the mobile breakpoint, and a
  // desktop with a mouse has room for the chrome and expects it.
  useImmersiveView(isTouchDevice());
  const queryClient = useQueryClient();
  const canvasRef = useRef<PaperCanvasHandle>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [tool, setTool] = useState<StrokeTool>('pen');
  // Each tool remembers its own width (index into TOOL_SIZES[tool]).
  const [sizeIndex, setSizeIndex] = useState<Record<StrokeTool, number>>({
    pen: 1,
    highlighter: 1,
    eraser: 1,
  });
  const currentSize = TOOL_SIZES[tool][sizeIndex[tool]];
  const [canvasState, setCanvasState] = useState({
    canUndo: false,
    canRedo: false,
    dirty: false,
    revision: 0,
  });
  // Select mode swaps the pen for picture handling. It is a mode rather than a
  // modifier because there is no mouse here: with a stylus there is no hover,
  // no right-click and no spare button to hold.
  const [selectMode, setSelectMode] = useState(false);
  const [selectedImageId, setSelectedImageId] = useState<string | null>(null);
  // Geometry of the drag in flight, before it is committed to pendingImageEdits.
  const [imagePreview, setImagePreview] = useState<{
    id: string;
    box: ImageBox;
  } | null>(null);
  // Picture transforms (move/resize/rotate/flip/lock) and deletions, committed
  // locally and held here — like strokes, they only reach the server on Save.
  // Edits are keyed by image id so the same picture can be nudged several times
  // before saving.
  const [staged, setStaged] = useState<StagedImages>(NO_STAGED_IMAGES);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  // Pages of this paper the device is still holding work for — the count the
  // Save button reports, and the reason it can be pressed on a page that is
  // itself clean.
  const [pendingPages, setPendingPages] = useState<Set<string>>(
    () => new Set()
  );
  // Pictures this device is still holding for the open paper because an
  // upload of them was refused. A refusal used to be completely silent: the
  // picture stayed on screen (drawn from the blob it was pasted from) until the
  // page was left, and was simply gone after that, with the bytes sitting in
  // IndexedDB that nothing would ever send again. Paper is the one feature
  // whose pictures cannot be re-taken from anywhere else, so a refused one has
  // to say so and stay retryable.
  const [refusedPhotos, setRefusedPhotos] = useState<StoredPhoto[]>([]);

  // The page is fitted into whatever the drawing area currently is, so its size
  // has to be measured rather than assumed (the sidebar reflows it without any
  // window resize).
  const stageRef = useRef<HTMLDivElement>(null);
  const [stage, setStage] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const measure = () =>
      setStage({ width: el.clientWidth, height: el.clientHeight });
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Contain-fit: the page keeps its A4 ratio and the leftover shows as bars.
  const box = fitPageBox({
    width: stage.width - PAGE_INSET * 2,
    height: stage.height - PAGE_INSET * 2,
  });

  // The tool to return to when the eraser is toggled back off.
  const prevToolRef = useRef<StrokeTool>('pen');
  const toggleEraser = useCallback(() => {
    setTool(current => {
      if (current === 'eraser') return prevToolRef.current;
      prevToolRef.current = current;
      return 'eraser';
    });
  }, []);

  const { data: paper } = useQuery({
    queryKey: ['paper', paperId],
    queryFn: () => api.paper.get(paperId),
  });
  const pages = paper?.pages ?? [];
  const currentPage = pages[currentIndex];

  // `staleTime: Infinity` because this data cannot go stale: nothing but this
  // client ever writes a page's strokes or pictures, so the server is only ever
  // *behind* the cache — never ahead of it. A refetch against a populated cache
  // can return equal-or-older content and nothing else, which makes it useless
  // at best and destructive at worst: since Save is manual, a page can sit
  // legitimately ahead of the server for a whole session, and a picture pasted
  // in that time exists only as an optimistic row here (the server has never
  // heard of it). React Query replaces query data wholesale on a successful
  // fetch, so one background refetch — 60s of idle plus an app-switch, which on
  // an iPad is just Tuesday — would drop that row and the picture would vanish
  // off the page. The only fetch that carries any information is the cold one
  // on a real cache miss, and that still happens.
  const { data: content } = useQuery({
    queryKey: ['paper', 'page', currentPage?.id],
    queryFn: () => api.paper.getPage(currentPage!.id),
    enabled: !!currentPage?.id,
    staleTime: Infinity,
  });

  // Page saves and picture writes go through the offline queue: paused while the
  // backend is unreachable, replayed when it is back, and always uploading the
  // *newest* state of the page rather than the one that happened to be pending
  // first (src/offline/pageStore.ts). They are enqueued rather than driven by a
  // hook because Save fires a burst of them — see `enqueuePaperWrite`.
  const addPage = usePaperPageAdd();

  const archiveRequested = paper?.archiveRequested ?? false;
  const setArchive = useMutation({
    mutationFn: (requested: boolean) =>
      api.paper.setArchiveRequested(paperId, requested),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['paper', paperId] });
      queryClient.invalidateQueries({ queryKey: ['paper'] });
    },
  });

  /** Write the page on screen to *this device* — strokes, the rendered snapshot
   * and the staged picture work — and nothing else.
   *
   * Nothing here touches the network. Drawing on a tablet with bad wifi was
   * unusable while leaving a page also uploaded it, so leaving a page (navigate,
   * back, add page, the tab going away) now only commits locally, and so does
   * the idle timer below. The server hears about a paper exactly when Save is
   * pressed — see `saveAll`.
   *
   * Never throws: unsaved strokes stay in the canvas's IndexedDB buffer and
   * staged picture work stays in `staged`, so a failure here loses nothing.
   */
  const commitLocal = async (): Promise<void> => {
    if (!currentPage) return;
    try {
      const data = await canvasRef.current?.getSaveData();
      if (data) {
        await storePageSave(
          currentPage.id,
          {
            strokes: data.strokes,
            width: data.width,
            height: data.height,
            revision: data.revision,
          },
          data.snapshot
        );
        // Write what we just stored straight into the page's cache entry.
        // Without this the entry keeps whatever the page held when it was
        // opened — empty, for a page written on for the first time — and
        // coming back to it later re-seeds the canvas from that stale copy, so
        // the page reads as blank. Drawing on the blank one would then
        // overwrite the real strokes, since a save replaces the column
        // outright. setQueryData (not an invalidate) because the client
        // already knows the answer: no refetch, and nothing races the page
        // currently under the pen.
        queryClient.setQueryData<PaperPageContent>(
          ['paper', 'page', currentPage.id],
          prev => ({
            strokes: data.strokes,
            width: data.width,
            height: data.height,
            // Strokes only — pictures are owned by their own endpoints — so
            // carry across whatever the cache already holds rather than
            // blanking the list.
            images: prev?.images ?? [],
          })
        );
      }
    } catch (e) {
      setSaveError(
        e instanceof Error ? e.message : 'Could not store this page'
      );
      return;
    }
    await refreshPending();
  };

  /** Send this whole paper to the server: every page the device is holding work
   * for, not just the one on screen.
   *
   * The only path that reaches the server. A paper is one thing to the person
   * drawing it — four pages of the same notebook — so one press of Save is the
   * whole notebook, including pages written on earlier and flipped away from.
   *
   * Mutations are fired, not awaited: while the backend is unreachable they
   * *pause* — their promises never settle — and awaiting one would leave the
   * editor spinning on "Saving…" for as long as the wifi is out. They all share
   * one lane, so a page's save cannot outrun the page's own creation.
   */
  const saveAll = async (): Promise<boolean> => {
    if (savingRef.current) return true;
    savingRef.current = true;
    setSaving(true);
    try {
      // The live canvas first, so the page under the pen is part of this save.
      await commitLocal();
      const held = await listPhotos().catch(() => [] as StoredPhoto[]);
      for (const page of pages) {
        const pending = await getPageSave(page.id);
        if (pending) {
          enqueuePaperWrite(
            queryClient,
            MUTATION_KEYS.paperPageSave,
            { pageId: page.id },
            {
              // Guarded by the revision: strokes drawn during the upload stay
              // dirty. Only the page on screen has a canvas to tell — another
              // page's flag lives in the record the upload clears.
              onSuccess: () => {
                if (page.id === currentPageIdRef.current) {
                  canvasRef.current?.markSaved(pending.meta.revision);
                }
              },
              onSettled: () => void refreshPending(),
            }
          );
        }

        const isOpen = page.id === staged.pageId;
        const edits = isOpen
          ? staged.edits
          : parseImageEditsBuffer(
              await idbGet(imageEditsBufferKey(page.id)).catch(() => undefined)
            );

        // Pictures this device is still holding for the page. Pasting one no
        // longer uploads it, so this is where the bytes go — and it doubles as
        // the rescue path for an orphan the boot sweep deliberately leaves
        // alone (see src/offline/photoQueue.ts).
        //
        // Handled before the plain edits loop below, and deliberately not by
        // it: a picture moved before it ever reached the server has its move
        // sitting in `edits` under the same id as its pending upload. Sending
        // that as a PATCH would 404 — there is no row yet — and the add that
        // follows would then create one back at the *original* pasted
        // position, undoing the move right as Save finished. The fix is to
        // fold any staged position into the upload itself.
        const heldForPage = held.filter(
          p =>
            p.target === 'paper' &&
            p.targetId === page.id &&
            !p.failed &&
            !!p.placement
        );
        const heldIds = new Set(heldForPage.map(p => p.id));
        for (const photo of heldForPage) {
          const edit = edits[photo.id];
          const box = edit
            ? {
                x: edit.x ?? photo.placement!.x,
                y: edit.y ?? photo.placement!.y,
                width: edit.width ?? photo.placement!.width,
                height: edit.height ?? photo.placement!.height,
              }
            : photo.placement!;
          enqueuePaperWrite(
            queryClient,
            MUTATION_KEYS.paperImageAdd,
            {
              imageId: photo.id,
              pageId: page.id,
              box,
              filename: photo.filename,
            },
            { onSettled: () => void refreshPending() }
          );
          // Rotation/flip/lock aren't accepted by the add endpoint. Enqueued
          // right behind it rather than before: the lane runs writes in the
          // order they were queued, so by the time this PATCH lands the row
          // the add just created is already there to apply it to.
          //
          // Picked explicitly rather than by stripping x/y/width/height: a
          // staged edit is built from PaperImageLayer's drag state, which
          // carries the picture's whole cached row (id, url, position…)
          // forward through every move — harmless when it rides along on a
          // PATCH to an existing row, but not something to invent a *second*
          // write to send on its own.
          // A move (or a resize) always carries the picture's rotation,
          // flipped and locked flags along for the ride, at whatever value
          // they already had — which for a picture that has never been
          // uploaded is exactly what the add above is about to create anyway
          // (the endpoint has no rotation/flip/lock parameters, so a fresh
          // row is always rotation 0, unflipped, unlocked). Comparing against
          // those same defaults, not just checking the field is present, is
          // what keeps a plain move from manufacturing a pointless PATCH.
          const rest: Partial<
            Pick<PageImage, 'rotation' | 'flipped' | 'locked'>
          > = {};
          if (edit?.rotation !== undefined && edit.rotation !== 0) {
            rest.rotation = edit.rotation;
          }
          if (edit?.flipped) rest.flipped = edit.flipped;
          if (edit?.locked) rest.locked = edit.locked;
          if (Object.keys(rest).length > 0) {
            enqueuePaperWrite(queryClient, MUTATION_KEYS.paperImageUpdate, {
              imageId: photo.id,
              pageId: page.id,
              edit: rest,
            });
          }
        }

        for (const [imageId, edit] of Object.entries(edits)) {
          if (heldIds.has(imageId)) continue; // folded into the add above
          enqueuePaperWrite(queryClient, MUTATION_KEYS.paperImageUpdate, {
            imageId,
            pageId: page.id,
            edit,
          });
        }
        if (Object.keys(edits).length > 0 && !isOpen) {
          idbDel(imageEditsBufferKey(page.id)).catch(() => {});
        }

        const deletes = isOpen
          ? staged.deletes
          : parseImageDeletesBuffer(
              await idbGet(imageDeletesBufferKey(page.id)).catch(
                () => undefined
              )
            );
        for (const imageId of deletes) {
          // Drop it from the cache as it goes, or clearing the staged list
          // below would put the picture back on screen until the DELETE lands.
          queryClient.setQueryData<PaperPageContent>(
            ['paper', 'page', page.id],
            prev =>
              prev
                ? { ...prev, images: prev.images.filter(i => i.id !== imageId) }
                : prev
          );
          void deleteOnServer(imageId, page.id);
        }
        if (deletes.length > 0 && !isOpen) {
          idbDel(imageDeletesBufferKey(page.id)).catch(() => {});
        }
      }
      setStaged(prev =>
        prev.pageId ? { ...prev, edits: {}, deletes: [] } : prev
      );
      setSaveError(null);
      // Only the grid needs refreshing. Invalidating the whole 'paper' prefix
      // would also refetch the page content being drawn on right now.
      queryClient.invalidateQueries({ queryKey: ['paper'], exact: true });
      return true;
    } catch (e) {
      setSaveError(
        e instanceof Error ? e.message : 'Could not save this paper'
      );
      return false;
    } finally {
      savingRef.current = false;
      setSaving(false);
      await refreshPending();
    }
  };

  // Effects and gesture callbacks read these through refs so they don't need to
  // be torn down and re-registered on every render.
  const commitRef = useRef(commitLocal);
  commitRef.current = commitLocal;
  const saveAllRef = useRef(saveAll);
  saveAllRef.current = saveAll;
  const currentPageIdRef = useRef<string | undefined>(undefined);
  currentPageIdRef.current = currentPage?.id;

  const navigate = async (direction: SwipeDirection) => {
    const res = resolveSwipe(direction, currentIndex, pages.length);
    if (res.index === currentIndex && !res.createPage) return;
    await commitLocal();
    if (res.createPage) {
      // Fired, not awaited — the mutation pauses while the backend is out of
      // reach and its promise would never settle, leaving a tap on "next page"
      // doing nothing at all. The page exists on the device the moment its id
      // is minted; the server hears about it when it can.
      addPage.mutate({ paperId, pageId: ulid() });
    }
    setCurrentIndex(res.index);
  };

  const handleBack = async () => {
    // Local only. Leaving a page is not a decision to sync it — that is what
    // Save is for — and on bad wifi the upload used to make Back feel broken.
    await commitLocal();
    onBack();
  };

  /** Append a page and jump to it. `pages.length` is the pre-insert count, which
   * is exactly the index the new page lands on. */
  const addNewPage = async () => {
    await commitLocal();
    const target = pages.length;
    // Fired, not awaited: see the note in the page-navigation handler above.
    addPage.mutate({ paperId, pageId: ulid() });
    setCurrentIndex(target);
  };

  // Best-effort *local* commit when the tab is hidden (iPad app-switch, screen
  // lock). It writes to the device and nothing more — an app-switch is not a
  // request to sync, and on a bad connection it is the worst possible moment
  // to try.
  useEffect(() => {
    const onHidden = () => {
      if (document.visibilityState === 'hidden') void commitRef.current();
    };
    document.addEventListener('visibilitychange', onHidden);
    return () => document.removeEventListener('visibilitychange', onHidden);
  }, []);

  // Store the page a couple of seconds after the pen stops. This is the whole
  // of "the page saves itself": instant, because it is two IndexedDB writes and
  // a canvas snapshot with no request in sight. The timer is reset by every
  // stroke (the revision changes), so it fires once the drawing pauses rather
  // than in the middle of it.
  useEffect(() => {
    if (!canvasState.dirty) return;
    const timer = setTimeout(
      () => void commitRef.current(),
      LOCAL_COMMIT_DELAY_MS
    );
    return () => clearTimeout(timer);
  }, [canvasState.dirty, canvasState.revision]);

  // Keyboard tool switching / undo. Apple Pencil's double-tap isn't exposed to
  // browsers, so this and the canvas's two-finger tap are the ways to reach the
  // eraser without the toolbar.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (
        el &&
        (el.tagName === 'INPUT' ||
          el.tagName === 'TEXTAREA' ||
          el.isContentEditable)
      ) {
        return;
      }
      if (e.ctrlKey || e.metaKey) {
        if (e.key.toLowerCase() === 'z') {
          e.preventDefault();
          if (e.shiftKey) canvasRef.current?.redo();
          else canvasRef.current?.undo();
        } else if (e.key.toLowerCase() === 's') {
          e.preventDefault();
          void saveAllRef.current();
        }
        return;
      }
      if (e.key === 'e') {
        e.preventDefault();
        toggleEraser();
      } else if (e.key === 'p') {
        setTool('pen');
      } else if (e.key === 'h') {
        setTool('highlighter');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [toggleEraser]);

  // A picture pasted while the backend was unreachable has no server URL yet —
  // it is a blob on this device. Object URLs are made here rather than stored
  // on the cached image, because a blob: URL written into the persisted cache
  // is dead the moment the tab reloads, and the picture would come back as a
  // broken image on a page that still has it.
  const [localUrls, setLocalUrls] = useState<Record<string, string>>({});
  const pendingImageIds = (content?.images ?? [])
    .filter(i => !i.url)
    .map(i => i.id)
    .join(',');
  useEffect(() => {
    const ids = pendingImageIds ? pendingImageIds.split(',') : [];
    if (ids.length === 0) {
      setLocalUrls(prev => {
        Object.values(prev).forEach(URL.revokeObjectURL);
        return {};
      });
      return;
    }
    let cancelled = false;
    const made: string[] = [];
    void (async () => {
      const next: Record<string, string> = {};
      for (const id of ids) {
        const stored = await getPhoto(id);
        if (!stored) continue;
        const url = URL.createObjectURL(stored.blob);
        made.push(url);
        next[id] = url;
      }
      if (cancelled) {
        made.forEach(URL.revokeObjectURL);
        return;
      }
      setLocalUrls(next);
    })();
    return () => {
      cancelled = true;
      made.forEach(URL.revokeObjectURL);
    };
  }, [pendingImageIds]);

  // SQLite hands booleans back as 0/1; the geometry module wants real booleans.
  // Depends on `content?.images`, not `content` itself: an idle-timer local
  // commit rewrites the page's cache entry every couple of seconds while the
  // pen is moving (see LOCAL_COMMIT_DELAY_MS below), and that write keeps the
  // same `images` array — only `strokes` changes. Depending on the whole
  // object made this recompute on every one of those commits, which gave the
  // canvas a new `images` prop identity mid-sentence and made it redraw from
  // scratch (see the `[images]` effect in PaperCanvas) — visible as strokes
  // being written flickering out and back while drawing.
  const images = useMemo<PageImage[]>(
    () =>
      (content?.images ?? []).map(i => ({
        id: i.id,
        url: i.url || localUrls[i.id] || '',
        x: i.x,
        y: i.y,
        width: i.width,
        height: i.height,
        rotation: i.rotation,
        flipped: !!i.flipped,
        locked: !!i.locked,
        position: i.position,
      })),
    [content?.images, localUrls]
  );

  // The staged work belongs to one page. Reading it through this guard rather
  // than straight out of state is what keeps a page flip from showing page A's
  // staged moves on page B for the render before the load resolves.
  const stagedForPage = staged.pageId === currentPage?.id;
  const pendingImageEdits = stagedForPage
    ? staged.edits
    : NO_STAGED_IMAGES.edits;
  const pendingImageDeletes = stagedForPage
    ? staged.deletes
    : NO_STAGED_IMAGES.deletes;

  // What the canvas and the overlay actually draw: the server's list with any
  // staged-but-unsaved transform applied, then the in-flight drag on top of
  // that, so a move tracks the finger without a round trip and neither needs
  // the network to show up. A staged deletion is simply gone from the list —
  // the picture has to leave the page the moment it is deleted, even though the
  // server won't hear about it until Save.
  const shownImages = useMemo(
    () =>
      images
        .filter(i => !pendingImageDeletes.includes(i.id))
        .map(i => {
          const pending = pendingImageEdits[i.id];
          const withPending = pending ? { ...i, ...pending } : i;
          return imagePreview && imagePreview.id === i.id
            ? { ...withPending, ...imagePreview.box }
            : withPending;
        }),
    [images, pendingImageEdits, pendingImageDeletes, imagePreview]
  );
  const selectedImage = shownImages.find(i => i.id === selectedImageId) ?? null;
  // A locked picture is deliberately invisible to the hit test, so tapping the
  // page can never reach one again. This is the way back to it.
  const lockedImages = shownImages.filter(i => i.locked);
  // A picture with no server url has never been uploaded — it is a blob this
  // device is holding — so the page is unsaved even if nothing has been drawn
  // or moved since it was pasted.
  const heldImageCount = (content?.images ?? []).filter(i => !i.url).length;
  const imagesDirty =
    Object.keys(pendingImageEdits).length > 0 ||
    pendingImageDeletes.length > 0 ||
    heldImageCount > 0;

  /** Stage a picture transform locally instead of syncing it straight away —
   * like strokes, it only reaches the server on Save (or on leaving the
   * page). Merged over whatever is already pending for this image so several
   * transforms can stack before a save flushes them. */
  const commitImageEdit = (id: string, data: PendingImageEdit) => {
    const pageId = currentPage?.id;
    if (!pageId) return;
    setStaged(prev => {
      const base = prev.pageId === pageId ? prev : { ...NO_STAGED_IMAGES };
      return {
        pageId,
        edits: { ...base.edits, [id]: { ...base.edits[id], ...data } },
        deletes: base.deletes,
      };
    });
    setImagePreview(null);
    // The page looks different now, so its snapshot is out of date — and the
    // snapshot is the whole of what the explorer grid and the Journal show.
    canvasRef.current?.markDirty();
  };

  /** Fired only from `saveAll`. A deletion is staged like every other picture
   * change, so the server hears about it with the rest of the paper.
   *
   * Not a queued mutation: unlike a save or a placement there is nothing here
   * to replay from — the picture is already gone from the page. A failure puts
   * the id back on the staged list instead, so the next Save tries again. */
  const deleteOnServer = async (id: string, pageId: string) => {
    try {
      // Deliberately no invalidate afterwards: the row was already dropped from
      // the cache on the way out, and an invalidate ignores staleTime — it
      // would refetch and take every *other* never-uploaded picture on the page
      // with it.
      await api.paper.deleteImage(id);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Could not delete it');
      setStaged(prev =>
        prev.pageId === pageId && !prev.deletes.includes(id)
          ? { ...prev, deletes: [...prev.deletes, id] }
          : prev
      );
    }
  };

  /** Take a picture off the page. Staged for Save like a move or a rotate —
   * except for one that has never reached the server, which is a blob this
   * device is holding and nothing else: that is dropped outright, because
   * there is no row for a later DELETE to find. */
  const deleteImage = async (id: string) => {
    const pageId = currentPage?.id;
    if (!pageId) return;
    setSelectedImageId(null);
    canvasRef.current?.markDirty();
    const onServer = !!(content?.images ?? []).find(i => i.id === id)?.url;
    if (!onServer) {
      await deletePhoto(id).catch(() => {});
      queryClient.setQueryData<PaperPageContent>(
        ['paper', 'page', pageId],
        prev =>
          prev
            ? { ...prev, images: prev.images.filter(i => i.id !== id) }
            : prev
      );
    }
    setStaged(prev => {
      const base = prev.pageId === pageId ? prev : { ...NO_STAGED_IMAGES };
      // A staged transform for a picture that is going away would 404 the save.
      const { [id]: _dropped, ...edits } = base.edits;
      return {
        pageId,
        edits,
        deletes:
          onServer && !base.deletes.includes(id)
            ? [...base.deletes, id]
            : base.deletes,
      };
    });
    await refreshPending();
  };

  /** Re-read what this device is still holding for this paper: which pages have
   * unsent work, and which pictures were refused.
   *
   * Scoped to the whole paper rather than the page on screen, and answered in
   * one pass over the two stores, because both questions are about the paper —
   * Save sends every page of it, and a refusal that repeats does so on every
   * page, so one Retry that clears the lot beats hunting for the pages carrying
   * one.
   *
   * Staged picture work needs no scan of its own: staging one marks the canvas
   * dirty, so leaving the page writes a page record here anyway, and the page
   * on screen is answered from `staged` directly. */
  const pageIdList = pages.map(p => p.id).join(',');
  const refreshPending = useCallback(async () => {
    const ids = new Set(pageIdList ? pageIdList.split(',') : []);
    if (ids.size === 0) {
      setPendingPages(new Set());
      setRefusedPhotos([]);
      return;
    }
    const [saves, held] = await Promise.all([
      listPageSaves().catch(
        () => [] as Awaited<ReturnType<typeof listPageSaves>>
      ),
      listPhotos().catch(() => [] as StoredPhoto[]),
    ]);
    const dirty = new Set<string>();
    for (const save of saves) {
      if (ids.has(save.pageId)) dirty.add(save.pageId);
    }
    const paperPhotos = held.filter(
      p =>
        p.target === 'paper' &&
        ids.has(p.targetId) &&
        // Every paper photo is stored with the box it was pasted into,
        // precisely so it can be put back where it was rather than guessed
        // at — one without a placement is not something we can re-place.
        !!p.placement
    );
    for (const photo of paperPhotos) {
      if (!photo.failed) dirty.add(photo.targetId);
    }
    setPendingPages(dirty);
    setRefusedPhotos(paperPhotos.filter(p => !!p.lastError));
  }, [pageIdList]);

  useEffect(() => {
    void refreshPending();
  }, [refreshPending]);

  /** Send the refused pictures again, each to the page it was pasted onto. The
   * refusal is cleared first: `failed` means "the server said no to this file",
   * which is a reason that can stop being true — a backend that has learned to
   * accept the format is exactly what makes a retry worth offering.
   *
   * Retry is an explicit press, so it goes straight out rather than waiting for
   * the next Save — but Save would pick these up too, now that clearing the
   * failure puts them back in the set it sends. */
  const retryRefused = async () => {
    for (const photo of refusedPhotos) {
      if (!photo.placement) continue;
      await clearFailure(photo.id);
      enqueuePaperWrite(
        queryClient,
        MUTATION_KEYS.paperImageAdd,
        {
          imageId: photo.id,
          pageId: photo.targetId,
          box: photo.placement,
          filename: photo.filename,
        },
        {
          onSuccess: () => {
            // Only the page on screen has a canvas to mark; another page's
            // snapshot is regenerated when it is next drawn on and saved.
            if (photo.targetId === currentPageIdRef.current) {
              canvasRef.current?.markDirty();
            }
          },
          onSettled: () => void refreshPending(),
        }
      );
    }
    await refreshPending();
  };

  /** Read a blob's pixel size, so a pasted picture can be placed at a sane
   * scale before it is uploaded. */
  const naturalSize = (file: Blob) =>
    new Promise<{ width: number; height: number }>(resolve => {
      const url = URL.createObjectURL(file);
      const el = new Image();
      el.onload = () => {
        URL.revokeObjectURL(url);
        resolve({ width: el.naturalWidth, height: el.naturalHeight });
      };
      el.onerror = () => {
        URL.revokeObjectURL(url);
        // Fall back to a square: better a placed picture than a failed paste.
        resolve({ width: 1, height: 1 });
      };
      el.src = url;
    });

  const addImage = async (file: Blob, filename?: string) => {
    if (!currentPage) return;
    const name = filename ?? pastedFilename(file.type);
    if (!name) {
      setSaveError(`Can't put a ${file.type || 'file of that type'} on a page`);
      return;
    }
    try {
      const size = await naturalSize(file);
      const box = fitPastedImage(size.width, size.height);
      const imageId = ulid();
      // The picture goes to the device before anything is sent. Paper is only
      // ever used on the tablet, and a pasted photo is the one thing on a page
      // that cannot be redrawn — so it is stored first and uploaded whenever
      // the backend is next in reach.
      await storePhoto(imageId, file, 'paper', currentPage.id, box);
      // On the page immediately, uploaded on Save. Nothing in a paper goes to
      // the server on its own any more, and a picture is no exception — the
      // bytes are on the device, which for paper is the only place they have
      // ever really lived.
      insertPendingPaperImage(queryClient, {
        imageId,
        pageId: currentPage.id,
        box,
      });
      // The picture is drawn by the canvas, so the page's snapshot no longer
      // matches it until the next save regenerates one.
      canvasRef.current?.markDirty();
      // Land in select mode with it chosen — pasting is always followed by
      // placing it.
      setSelectMode(true);
      setSelectedImageId(imageId);
      await refreshPending();
    } catch (e) {
      setSaveError(
        e instanceof Error ? e.message : 'Could not add the picture'
      );
    }
  };

  const addImageRef = useRef(addImage);
  addImageRef.current = addImage;

  /** Paste whatever picture is on the clipboard onto the middle of the page.
   *
   * A button rather than only a keyboard/`paste`-event path because on an iPad
   * there is neither: there is no Cmd+V without a keyboard, and press-and-hold
   * on the canvas offers to select text that isn't there. Read straight out of
   * the click handler so it runs inside the user gesture — Safari requires that
   * and puts up its own "Paste" confirmation. */
  const pasteFromClipboard = async () => {
    const read = navigator.clipboard?.read;
    if (!read) {
      setSaveError(
        'This browser will not let a page read the clipboard — use 🖼 Add instead'
      );
      return;
    }
    try {
      const items = await navigator.clipboard.read();
      for (const item of items) {
        const mime = item.types.find(t => t.startsWith('image/'));
        if (!mime) continue;
        const blob = await item.getType(mime);
        await addImage(blob, pastedFilename(mime) ?? undefined);
        return;
      }
      setSaveError('There is no picture on the clipboard');
    } catch (e) {
      setSaveError(
        e instanceof Error && e.message
          ? `Could not read the clipboard (${e.message}) — use 🖼 Add instead`
          : 'Could not read the clipboard — use 🖼 Add instead'
      );
    }
  };

  // Paste a picture straight onto the page. Registered on the window because
  // the canvas isn't focusable — there is nowhere for a paste to land otherwise.
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const item = Array.from(e.clipboardData?.items ?? []).find(
        i => i.kind === 'file' && i.type.startsWith('image/')
      );
      const file = item?.getAsFile();
      if (!file) return;
      e.preventDefault();
      void addImageRef.current(file);
    };
    window.addEventListener('paste', onPaste);
    return () => window.removeEventListener('paste', onPaste);
  }, []);

  // A selection belongs to one page; carrying it across would point the action
  // bar at a picture that is no longer on screen. Staged picture edits are
  // page-scoped too, but unlike strokes they have no server copy to fall back
  // on until saved, so they're reloaded from the same kind of local buffer
  // strokes use (imageEditsBufferKey) rather than simply discarded — a save
  // that failed on the way out must not quietly lose a moved picture.
  useEffect(() => {
    setSelectedImageId(null);
    setImagePreview(null);
    const pageId = currentPage?.id;
    if (!pageId) {
      setStaged(NO_STAGED_IMAGES);
      return;
    }
    let cancelled = false;
    void Promise.all([
      idbGet(imageEditsBufferKey(pageId)).catch(() => undefined),
      idbGet(imageDeletesBufferKey(pageId)).catch(() => undefined),
    ]).then(([rawEdits, rawDeletes]) => {
      if (cancelled) return;
      setStaged({
        pageId,
        edits: parseImageEditsBuffer(rawEdits),
        deletes: parseImageDeletesBuffer(rawDeletes),
      });
    });
    return () => {
      cancelled = true;
    };
  }, [currentPage?.id]);

  // Mirror staged picture work into IndexedDB, the same safety net the stroke
  // buffer gives drawing: a save that never reaches the server (closed tab,
  // dead network) must not silently drop a moved/rotated/deleted picture.
  //
  // Keyed on the page the staged work *belongs to*, not the page on screen.
  // Those differ for one render after a page flip, and writing then put page
  // A's staged moves into page B's buffer.
  useEffect(() => {
    const pageId = staged.pageId;
    if (!pageId) return;
    const editsKey = imageEditsBufferKey(pageId);
    if (Object.keys(staged.edits).length === 0) {
      idbDel(editsKey).catch(() => {});
    } else {
      idbSet(editsKey, JSON.stringify(staged.edits)).catch(() => {});
    }
    const deletesKey = imageDeletesBufferKey(pageId);
    if (staged.deletes.length === 0) {
      idbDel(deletesKey).catch(() => {});
    } else {
      idbSet(deletesKey, JSON.stringify(staged.deletes)).catch(() => {});
    }
  }, [staged]);

  // Memoised on the query result, whose identity React Query keeps stable
  // unless the data actually changed. The canvas adopts these on identity
  // change, so re-deriving them every render would re-seed it constantly and
  // throw away undo history.
  const initialStrokes = useMemo(
    () => (content ? parseStrokes(content.strokes) : []),
    [content]
  );
  const initialSize = useMemo(
    () =>
      content && content.width && content.height
        ? { width: content.width, height: content.height }
        : null,
    [content]
  );

  // `shrink-0` matters as much as the padding: the bar scrolls sideways when it
  // runs out of room (it is the only navigation there is in immersive mode), and
  // without it the buttons would squash instead. `min-h-[44px]` on a touch
  // screen is the same target size the rest of the app uses.
  // How many pages of this paper are waiting to go to the server: the ones the
  // device is already holding a record for, plus the page under the pen if what
  // is on it has not been committed yet.
  const unsavedPages =
    pendingPages.size +
    (currentPage &&
    !pendingPages.has(currentPage.id) &&
    (canvasState.dirty || imagesDirty)
      ? 1
      : 0);

  const btn =
    'shrink-0 px-3 py-1.5 min-h-[44px] md:min-h-0 rounded-md text-sm font-medium transition-colors disabled:opacity-40 bg-[var(--color-surface)] hover:bg-white/10';
  const toolBtn = (active: boolean) =>
    active
      ? 'px-3 py-1.5 rounded-md text-sm font-medium transition-colors bg-[var(--color-primary)] text-[var(--color-bg)]'
      : btn;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-white/10 shrink-0 overflow-x-auto overscroll-x-contain">
        <button onClick={handleBack} className={btn}>
          ‹ Back
        </button>
        <button
          onClick={() => setArchive.mutate(!archiveRequested)}
          className={toolBtn(archiveRequested)}
          title={
            archiveRequested
              ? 'Flagged to move to the Journal at 4am — tap to keep here'
              : 'Move this paper to the Journal (happens at 4am)'
          }
        >
          📓 {archiveRequested ? 'To journal ✓' : 'To journal'}
        </button>
        <button
          onClick={() => {
            setSelectMode(v => !v);
            setSelectedImageId(null);
          }}
          className={toolBtn(selectMode)}
          title={
            selectMode
              ? 'Back to drawing'
              : 'Select and move pictures instead of drawing'
          }
        >
          {selectMode ? '✋ Pictures' : '✋ Pictures'}
        </button>
        {selectMode && lockedImages.length > 0 && (
          <button
            onClick={() => {
              const at = lockedImages.findIndex(i => i.id === selectedImageId);
              setSelectedImageId(
                lockedImages[(at + 1) % lockedImages.length].id
              );
            }}
            className={btn}
            title="Step through locked pictures to unlock one"
          >
            🔒 {lockedImages.length}
          </button>
        )}
        <button
          onClick={() => imageInputRef.current?.click()}
          className={btn}
          title="Add a picture from a file"
        >
          🖼 Add
        </button>
        <button
          onClick={() => void pasteFromClipboard()}
          className={btn}
          title="Paste the picture on the clipboard into the middle of the page"
        >
          📋 Paste
        </button>
        <input
          ref={imageInputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif"
          className="hidden"
          onChange={e => {
            const file = e.target.files?.[0];
            if (file) void addImage(file, file.name);
            e.target.value = '';
          }}
        />
        <div className="ml-auto flex items-center gap-2 shrink-0">
          {/* Fixed-width slot. The tools used to sit in this bar and this text
           * popped in and out on every autosave, reflowing the row out from
           * under the stylus — the reserved width is what keeps the bar (and
           * now the floating panel, which carries no status at all) still. */}
          <span className="text-xs opacity-60 w-20 text-right shrink-0">
            {saveStatusLabel(saving, unsavedPages)}
          </span>
          <button
            onClick={() => void saveAll()}
            disabled={saving || unsavedPages === 0}
            className={btn}
            title="Save every page of this paper (Ctrl+S) — nothing is sent to the server until you do"
          >
            💾 Save
          </button>
          <button
            onClick={() => navigate('prev')}
            disabled={currentIndex === 0}
            className={btn}
            title="Previous page"
          >
            ‹
          </button>
          <span className="text-sm tabular-nums opacity-70 min-w-[3.5rem] text-center">
            {pages.length ? `${currentIndex + 1} / ${pages.length}` : '–'}
          </span>
          <button
            onClick={() => navigate('next')}
            disabled={currentIndex >= pages.length - 1}
            className={btn}
            title="Next page"
          >
            ›
          </button>
          <button
            onClick={addNewPage}
            disabled={addPage.isPending}
            className={btn}
            title="Add a new page at the end"
          >
            ＋ Page
          </button>
        </div>
      </div>

      {saveError && (
        <div className="flex items-center gap-3 px-3 py-2 text-sm bg-red-600/20 border-b border-red-600/40 shrink-0">
          <span className="flex-1">
            {saveError} — your strokes are still held on this device, so nothing
            is lost.
          </span>
          <button onClick={() => void saveAll()} className={btn}>
            Retry
          </button>
          <button onClick={() => setSaveError(null)} className={btn}>
            Dismiss
          </button>
        </div>
      )}

      {refusedPhotos.length > 0 && (
        <div className="flex items-center gap-3 px-3 py-2 text-sm bg-amber-500/20 border-b border-amber-500/40 shrink-0">
          <span className="flex-1">
            {refusedPhotos.length === 1
              ? 'A picture in this paper never reached the server'
              : `${refusedPhotos.length} pictures in this paper never reached the server`}{' '}
            ({refusedPhotos[0].lastError}) — still held on this device.
          </span>
          <button onClick={() => void retryRefused()} className={btn}>
            Retry pictures
          </button>
        </div>
      )}

      {/* Drawing surface. The page is an A4 sheet fitted into this area and
       * centred; the bars left over on two sides are deliberate — a page that
       * stretched to the viewport changed shape with the device, and the ink
       * saved on it came back distorted. */}
      <div
        ref={stageRef}
        className="flex-1 relative overflow-hidden bg-neutral-200"
      >
        {currentPage && content && box.width > 0 ? (
          <div
            className="absolute bg-white shadow-md rounded-sm overflow-hidden"
            style={{
              left: box.left + PAGE_INSET,
              top: box.top + PAGE_INSET,
              width: box.width,
              height: box.height,
            }}
          >
            <PaperCanvas
              key={currentPage.id}
              ref={canvasRef}
              pageId={currentPage.id}
              images={shownImages}
              initialStrokes={initialStrokes}
              initialSize={initialSize}
              tool={tool}
              size={currentSize}
              onSwipe={navigate}
              onToggleEraser={toggleEraser}
              onStateChange={setCanvasState}
            />
            {/* Only mounted in select mode, so it can never swallow a stroke. */}
            {selectMode && (
              <PaperImageLayer
                images={shownImages}
                scale={box.width / PAGE_WIDTH}
                selectedId={selectedImageId}
                onSelect={setSelectedImageId}
                onPreview={(id, next) => setImagePreview({ id, box: next })}
                onCommit={(id, next) => commitImageEdit(id, next)}
              />
            )}
          </div>
        ) : (
          <div className="flex items-center justify-center w-full h-full text-neutral-500">
            Loading…
          </div>
        )}

        {selectMode && selectedImage && (
          <PaperImageActions
            image={selectedImage}
            onRotate={delta =>
              commitImageEdit(selectedImage.id, {
                rotation: snapRotation(rotateBy(selectedImage.rotation, delta)),
              })
            }
            onFlip={() =>
              commitImageEdit(selectedImage.id, {
                flipped: !selectedImage.flipped,
              })
            }
            onToggleLock={() =>
              commitImageEdit(selectedImage.id, {
                locked: !selectedImage.locked,
              })
            }
            onDelete={() => void deleteImage(selectedImage.id)}
          />
        )}

        <PaperToolPanel
          tool={tool}
          onToolChange={setTool}
          sizeIndex={sizeIndex[tool]}
          onSizeIndexChange={i =>
            setSizeIndex(prev => ({ ...prev, [tool]: i }))
          }
          canUndo={canvasState.canUndo}
          canRedo={canvasState.canRedo}
          onUndo={() => canvasRef.current?.undo()}
          onRedo={() => canvasRef.current?.redo()}
          bounds={stage}
        />
      </div>
    </div>
  );
}
