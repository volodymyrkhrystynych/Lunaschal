import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react';
import { get as idbGet, set as idbSet, del as idbDel } from 'idb-keyval';
import {
  PAGE_HEIGHT,
  PAGE_WIDTH,
  parseBuffer,
  serializeBuffer,
  serializeStrokes,
  strokeColor,
  toPageSpaceStrokes,
  type InkPalette,
  type Size,
  type Stroke,
  type StrokeTool,
  type SwipeDirection,
} from '@/lib/paper';
import { strokePathData } from '@/lib/inkPath';
import { InkSurface, type InkSurfaceHandle } from '@/components/ink/InkSurface';
import type { PageImage } from '@/lib/paperImages';

const PAGE_BG = '#ffffff';
/** What a page calls ink when a stroke does not say — which is every stroke
 * written before there was a colour picker. */
const PAPER_PALETTE: InkPalette = {
  ink: '#111111',
  highlight: '#ffe14d',
  highlightAlpha: 0.4,
};

const PAGE_SPACE: Size = { width: PAGE_WIDTH, height: PAGE_HEIGHT };

/** Width of the rendered page snapshot, in pixels — an A4 sheet at about
 * 150dpi. A fixed size on purpose: it used to be whatever the canvas happened
 * to be on screen, so the same page produced a different thumbnail on a laptop
 * and on a phone. */
const SNAPSHOT_WIDTH = 1240;

const bufferKey = (pageId: string) => `paper-page-${pageId}`;

/** One array, not a fresh `[]` per render. The ink surface re-seeds — and
 * discards its undo history — whenever the identity of `strokes` changes, so a
 * literal here would reset the page on every render its parent happened to do. */
const NO_STROKES: Stroke[] = [];
const NO_IMAGES: PageImage[] = [];

export interface PaperSaveData {
  strokes: string;
  width: number;
  height: number;
  snapshot: Blob;
  /** Edit counter at the moment this snapshot was taken. Passed back to
   * `markSaved` so strokes drawn while the upload was in flight are not
   * wrongly considered saved. */
  revision: number;
}

export interface PaperSurfaceHandle {
  undo: () => void;
  redo: () => void;
  /** Snapshot + strokes for upload, or null if the page is not dirty. */
  getSaveData: () => Promise<PaperSaveData | null>;
  /** Clear the dirty flag and discard the local (IndexedDB) buffer, but only if
   * nothing has been drawn since `revision` was issued. */
  markSaved: (revision: number) => void;
  /** Treat the page as changed without a stroke being drawn. A picture added,
   * moved or deleted changes what the page looks like, and the snapshot is what
   * the explorer grid and the Journal filmstrip show — without this, a page
   * whose only content is a photo has a blank thumbnail until something is
   * drawn on it. */
  markDirty: () => void;
}

interface PaperSurfaceProps {
  pageId: string;
  /** Pictures pasted onto the page, drawn beneath the ink. Interaction lives in
   * the DOM overlay above this surface, not here — see PaperImageLayer. */
  images?: PageImage[];
  initialStrokes: Stroke[];
  /** Coordinate space the stored strokes are in. Page-space rows report the
   * fixed page size; a row saved before the A4 page space reports the CSS-pixel
   * box it was drawn in, and is converted on read (see toPageSpaceStrokes). */
  initialSize: { width: number; height: number } | null;
  tool: StrokeTool;
  /** Base width for the active tool, in page units. */
  size: number;
  color?: string;
  onSwipe: (direction: SwipeDirection) => void;
  /** Two-finger tap on the page — toggles the eraser (see isTap). */
  onToggleEraser?: () => void;
  onStateChange?: (s: {
    canUndo: boolean;
    canRedo: boolean;
    dirty: boolean;
    revision: number;
  }) => void;
}

/** Place a picture the way `src/lib/paperImages.ts` says it sits: rotated and
 * mirrored about its own centre, which the DOM overlay's transform and the hit
 * test both assume. Written once and applied twice — as an SVG transform for
 * the page, and as canvas calls for the snapshot. */
const imageTransform = (img: PageImage): string =>
  `translate(${img.x + img.width / 2} ${img.y + img.height / 2}) ` +
  `rotate(${img.rotation}) ` +
  (img.flipped ? 'scale(-1 1) ' : '') +
  `translate(${-img.width / 2} ${-img.height / 2})`;

/**
 * A page of the Paper editor: the shared ink surface, plus everything that is
 * specific to a page that gets *saved* — the on-device stroke buffer, the
 * pictures under the ink, and the rendered snapshot.
 *
 * The drawing itself — pointer capture, the live stroke, the SVG — lives in
 * src/components/ink/InkSurface.tsx and is shared with the newspaper reader.
 */
export const PaperSurface = forwardRef<PaperSurfaceHandle, PaperSurfaceProps>(
  function PaperSurface(
    {
      pageId,
      images = NO_IMAGES,
      initialStrokes,
      initialSize,
      tool,
      size,
      color,
      onSwipe,
      onToggleEraser,
      onStateChange,
    },
    ref
  ) {
    const inkRef = useRef<InkSurfaceHandle>(null);
    // Null until the on-device buffer has been looked for. Ink saved before the
    // page space existed is converted on read; a row already in page space
    // passes through untouched.
    const [seed, setSeed] = useState<Stroke[] | null>(null);
    const fromBufferRef = useRef(false);

    // Prefer an unsaved local buffer over the server copy if one exists.
    useEffect(() => {
      let cancelled = false;
      const stored = toPageSpaceStrokes(initialStrokes, initialSize);
      idbGet(bufferKey(pageId))
        .then(buf => {
          if (cancelled) return;
          const buffered = parseBuffer(buf, initialSize);
          fromBufferRef.current = Boolean(buffered);
          setSeed(buffered ?? stored);
        })
        .catch(() => {
          if (!cancelled) setSeed(stored);
        });
      return () => {
        cancelled = true;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pageId]);

    // A buffer on the device is by definition unsaved work, so the page is
    // dirty the moment it is adopted.
    useEffect(() => {
      if (seed && fromBufferRef.current) {
        fromBufferRef.current = false;
        inkRef.current?.markDirty();
      }
    }, [seed]);

    // Content arriving after mount (a refetch landing while this surface is up)
    // still has to be converted before the ink layer can adopt it.
    const [adopted, setAdopted] = useState<Stroke[] | null>(null);
    const seenRef = useRef(initialStrokes);
    useEffect(() => {
      if (initialStrokes === seenRef.current) return;
      seenRef.current = initialStrokes;
      setAdopted(toPageSpaceStrokes(initialStrokes, initialSize));
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [initialStrokes]);

    // Decoded <img> elements, keyed by URL. The page itself shows the pictures
    // as SVG <image> and needs none of this — but an <svg> rasterized through
    // an <img> cannot fetch its own references, so the snapshot draws them
    // itself and needs them decoded and to hand.
    const imageElsRef = useRef(new Map<string, HTMLImageElement>());
    useEffect(() => {
      const cache = imageElsRef.current;
      for (const img of images) {
        if (cache.has(img.url)) continue;
        const el = new Image();
        el.src = img.url;
        cache.set(img.url, el);
      }
    }, [images]);

    const backdrop = useMemo(
      () => (
        <>
          <rect
            x={0}
            y={0}
            width={PAGE_WIDTH}
            height={PAGE_HEIGHT}
            fill={PAGE_BG}
          />
          {images.map(img => (
            <image
              key={img.id}
              href={img.url}
              x={0}
              y={0}
              width={img.width}
              height={img.height}
              transform={imageTransform(img)}
              preserveAspectRatio="none"
            />
          ))}
        </>
      ),
      [images]
    );

    const persistBuffer = useCallback(
      (strokes: Stroke[]) => {
        idbSet(bufferKey(pageId), serializeBuffer(strokes)).catch(() => {});
      },
      [pageId]
    );

    /** Render the page to a PNG.
     *
     * The ink is filled from the *same* path data the page is drawn with, so
     * the thumbnail cannot drift from what is on screen. Serializing the SVG
     * and loading it through an `<img>` would have been shorter and would have
     * silently dropped every picture: an SVG rasterized that way is not allowed
     * to fetch its own `href`s.
     */
    const renderSnapshot = useCallback(
      (strokes: Stroke[]): Promise<Blob | null> =>
        new Promise(resolve => {
          const canvas = document.createElement('canvas');
          canvas.width = SNAPSHOT_WIDTH;
          canvas.height = Math.round(
            (SNAPSHOT_WIDTH * PAGE_HEIGHT) / PAGE_WIDTH
          );
          const ctx = canvas.getContext('2d');
          if (!ctx) return resolve(null);
          const scale = canvas.width / PAGE_WIDTH;
          ctx.fillStyle = PAGE_BG;
          ctx.fillRect(0, 0, canvas.width, canvas.height);
          ctx.setTransform(scale, 0, 0, scale, 0, 0);

          for (const img of images) {
            const el = imageElsRef.current.get(img.url);
            if (!el?.complete || !el.naturalWidth) continue;
            ctx.save();
            ctx.translate(img.x + img.width / 2, img.y + img.height / 2);
            ctx.rotate((img.rotation * Math.PI) / 180);
            if (img.flipped) ctx.scale(-1, 1);
            ctx.drawImage(
              el,
              -img.width / 2,
              -img.height / 2,
              img.width,
              img.height
            );
            ctx.restore();
          }

          // Path2D is in every browser this runs in; jsdom has none, and a
          // snapshot without ink is a better test artefact than a crash.
          if (typeof Path2D !== 'undefined') {
            for (const stroke of strokes) {
              const d = strokePathData(stroke);
              if (!d) continue;
              ctx.fillStyle = strokeColor(stroke, PAPER_PALETTE);
              ctx.globalAlpha =
                stroke.tool === 'highlighter'
                  ? PAPER_PALETTE.highlightAlpha
                  : 1;
              ctx.fill(new Path2D(d));
            }
          }
          canvas.toBlob(resolve, 'image/png');
        }),
      [images]
    );

    useImperativeHandle(ref, () => ({
      undo: () => inkRef.current?.undo(),
      redo: () => inkRef.current?.redo(),
      markDirty: () => inkRef.current?.markDirty(),
      markSaved: (revision: number) => {
        const state = inkRef.current?.getState();
        // A stroke landed while the upload was in flight — the page is still
        // dirty and the buffer must survive for the next save.
        if (!state || state.revision !== revision) return;
        inkRef.current?.markSaved(revision);
        idbDel(bufferKey(pageId)).catch(() => {});
      },
      getSaveData: async () => {
        const state = inkRef.current?.getState();
        if (!state || !state.dirty) return null;
        const snapshot = await renderSnapshot(state.strokes);
        if (!snapshot) return null;
        return {
          strokes: serializeStrokes(state.strokes),
          // Always the fixed page space — the size column is what tells a
          // later load whether the row needs converting.
          width: PAGE_WIDTH,
          height: PAGE_HEIGHT,
          snapshot,
          revision: state.revision,
        };
      },
    }));

    return (
      <InkSurface
        ref={inkRef}
        space={PAGE_SPACE}
        strokes={adopted ?? seed ?? NO_STROKES}
        tool={tool}
        size={size}
        color={color}
        palette={PAPER_PALETTE}
        // The page is the screen: nothing scrolls, so a finger is free to mean
        // a page flip or an eraser toggle instead.
        touchPolicy="exclusive"
        backdrop={backdrop}
        onEdit={persistBuffer}
        onStateChange={onStateChange}
        onSwipe={onSwipe}
        onToggleEraser={onToggleEraser}
        className="bg-white touch-none"
        label="Page"
      />
    );
  }
);
