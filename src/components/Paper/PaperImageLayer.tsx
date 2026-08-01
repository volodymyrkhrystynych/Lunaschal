import { useRef, useState } from 'react';
import {
  imageAt,
  MIN_IMAGE_SIZE,
  moveImage,
  resizeImageToPoint,
  type ImageBox,
  type PageImage,
} from '@/lib/paperImages';
import { PAGE_WIDTH } from '@/lib/paper';

/** Size of the corner grab handle, in CSS pixels. Matches the 44px touch
 * target the rest of the app uses — this is dragged with a fingertip. */
const HANDLE_PX = 44;

interface PaperImageLayerProps {
  images: PageImage[];
  /** CSS pixels per page unit — the same factor the canvas renders with. */
  scale: number;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  /** Live geometry while a drag is in flight; not yet persisted. */
  onPreview: (id: string, box: ImageBox) => void;
  /** Drag finished — persist this geometry. */
  onCommit: (id: string, box: ImageBox) => void;
}

/**
 * Pointer handling for pasted pictures, as a DOM layer above the ink canvas.
 *
 * The pictures themselves are drawn *by the canvas*, beneath the ink; this
 * layer is transparent and exists only to be grabbed. Keeping interaction out
 * of the canvas is what lets the handles be real 44px touch targets instead of
 * hit-tested pixels, and leaves the drawing pointer logic untouched.
 *
 * The layer is only mounted in select mode, so it can never intercept a stroke.
 */
export function PaperImageLayer({
  images,
  scale,
  selectedId,
  onSelect,
  onPreview,
  onCommit,
}: PaperImageLayerProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<{
    id: string;
    mode: 'move' | 'resize';
    /** Page-space pointer position at the last move, for a delta. */
    last: { x: number; y: number };
    box: ImageBox;
  } | null>(null);

  // Client coords -> page space, via the layer's own box (which is the page).
  const toPage = (e: { clientX: number; clientY: number }) => {
    const r = rootRef.current?.getBoundingClientRect();
    if (!r) return { x: 0, y: 0 };
    return { x: (e.clientX - r.left) / scale, y: (e.clientY - r.top) / scale };
  };

  const startMove = (e: React.PointerEvent) => {
    const p = toPage(e);
    // Locked images are skipped by imageAt, so a tap over one falls through to
    // whatever is beneath it — or deselects.
    const hit = imageAt(images, p);
    if (!hit) {
      onSelect(null);
      return;
    }
    e.preventDefault();
    onSelect(hit.id);
    setDrag({ id: hit.id, mode: 'move', last: p, box: hit });
    try {
      (e.target as Element).setPointerCapture?.(e.pointerId);
    } catch {
      // Pointer capture is a nicety; a drag still tracks without it.
    }
  };

  const startResize = (e: React.PointerEvent, img: PageImage) => {
    e.preventDefault();
    e.stopPropagation();
    setDrag({ id: img.id, mode: 'resize', last: toPage(e), box: img });
    try {
      (e.target as Element).setPointerCapture?.(e.pointerId);
    } catch {
      /* see above */
    }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag) return;
    const p = toPage(e);
    const box =
      drag.mode === 'move'
        ? moveImage(drag.box, p.x - drag.last.x, p.y - drag.last.y)
        : resizeImageToPoint(drag.box, p);
    setDrag({ ...drag, mode: drag.mode, last: p, box });
    onPreview(drag.id, box);
  };

  const endDrag = () => {
    if (!drag) return;
    onCommit(drag.id, drag.box);
    setDrag(null);
  };

  const selected = images.find(i => i.id === selectedId) ?? null;

  return (
    <div
      ref={rootRef}
      data-testid="paper-image-layer"
      className="absolute inset-0 touch-none"
      onPointerDown={startMove}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
    >
      {selected && (
        <div
          data-testid="paper-image-selection"
          className="absolute border-2 border-[var(--color-primary)] pointer-events-none"
          style={{
            left: selected.x * scale,
            top: selected.y * scale,
            width: selected.width * scale,
            height: selected.height * scale,
            transform: `rotate(${selected.rotation}deg) scaleX(${
              selected.flipped ? -1 : 1
            })`,
            transformOrigin: 'center',
          }}
        />
      )}
      {selected && (
        <button
          type="button"
          aria-label="Resize image"
          data-testid="paper-image-resize"
          onPointerDown={e => startResize(e, selected)}
          className="absolute rounded-full bg-[var(--color-primary)] border-2 border-white shadow touch-none"
          style={{
            width: HANDLE_PX,
            height: HANDLE_PX,
            // Anchored on the unrotated bottom-right corner. Resizing scales
            // about the centre, so the handle only has to be reachable — it
            // does not have to track a rotated corner exactly.
            left: (selected.x + selected.width) * scale - HANDLE_PX / 2,
            top: (selected.y + selected.height) * scale - HANDLE_PX / 2,
          }}
        />
      )}
    </div>
  );
}

/** Smallest sensible on-screen size for the resize floor, exported so the
 * editor can describe the limit without importing the geometry module. */
export const MIN_IMAGE_PAGE_FRACTION = MIN_IMAGE_SIZE / PAGE_WIDTH;
