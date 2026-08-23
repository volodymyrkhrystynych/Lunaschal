// Geometry for pictures pasted onto a paper page. Pure — no DOM, no canvas —
// so the fiddly parts (rotation, mirroring, hit-testing) are unit-testable in
// the node environment, the same reason src/lib/paper.ts exists.
//
// Everything here is in the page's A4 coordinate space (PAGE_WIDTH x
// PAGE_HEIGHT), the same space strokes live in. An image is stored as an
// unrotated box (x, y, width, height) plus a rotation and a mirror flag, both
// applied about the box's own centre.

import { PAGE_HEIGHT, PAGE_WIDTH } from './paper';

export interface PageImage {
  id: string;
  url: string;
  x: number;
  y: number;
  width: number;
  height: number;
  /** Degrees clockwise. */
  rotation: number;
  /** Mirrored left-to-right about its own centre. */
  flipped: boolean;
  locked: boolean;
  position: number;
}

/** Placement geometry only — what a transform returns and a PATCH sends. */
export type ImageBox = Pick<PageImage, 'x' | 'y' | 'width' | 'height'>;

export interface Point {
  x: number;
  y: number;
}

/** Quarter turns only. Finer steps were tried and dropped: getting a photo
 * upright is the whole job, and 90s do that in at most three taps. */
export const ROTATION_STEP = 90;

/** Nothing may be resized smaller than this, in page units (~5mm). A zero-size
 * image is unselectable and therefore unrecoverable. */
export const MIN_IMAGE_SIZE = 50;

/** A pasted image lands at this fraction of the page, whatever its pixel size —
 * so a 64px icon and a 4000px photo both arrive workably sized. */
const PASTE_FRACTION = 0.6;

export function normalizeRotation(deg: number): number {
  if (!Number.isFinite(deg)) return 0;
  return ((deg % 360) + 360) % 360;
}

export function rotateBy(deg: number, delta: number): number {
  return normalizeRotation(deg + delta);
}

/** Nearest multiple of ROTATION_STEP. Used to pull a hand-set angle back onto
 * the grid, and to keep repeated steps from drifting. */
export function snapRotation(deg: number): number {
  if (!Number.isFinite(deg)) return 0;
  return normalizeRotation(Math.round(deg / ROTATION_STEP) * ROTATION_STEP);
}

export function imageCenter(img: ImageBox): Point {
  return { x: img.x + img.width / 2, y: img.y + img.height / 2 };
}

/** Where a pasted image should land: centred on the page, scaled to a
 * consistent fraction of it, aspect preserved. */
export function fitPastedImage(
  naturalWidth: number,
  naturalHeight: number
): ImageBox {
  const safeW = naturalWidth > 0 ? naturalWidth : 1;
  const safeH = naturalHeight > 0 ? naturalHeight : 1;
  const scale = Math.min(
    (PAGE_WIDTH * PASTE_FRACTION) / safeW,
    (PAGE_HEIGHT * PASTE_FRACTION) / safeH
  );
  const width = Math.max(MIN_IMAGE_SIZE, safeW * scale);
  const height = Math.max(MIN_IMAGE_SIZE, safeH * scale);
  return {
    x: (PAGE_WIDTH - width) / 2,
    y: (PAGE_HEIGHT - height) / 2,
    width,
    height,
  };
}

interface Placed extends ImageBox {
  rotation: number;
  flipped: boolean;
}

/** A page point expressed in the image's own unrotated space, origin at its
 * top-left corner. Inside the image iff both coords fall within its size. */
export function toImageLocal(img: Placed, p: Point): Point {
  const c = imageCenter(img);
  // Inverse rotation: undo what rendering applies.
  const rad = (-img.rotation * Math.PI) / 180;
  const dx = p.x - c.x;
  const dy = p.y - c.y;
  let lx = dx * Math.cos(rad) - dy * Math.sin(rad);
  const ly = dx * Math.sin(rad) + dy * Math.cos(rad);
  if (img.flipped) lx = -lx;
  return { x: lx + img.width / 2, y: ly + img.height / 2 };
}

export function hitTestImage(img: Placed, p: Point): boolean {
  const l = toImageLocal(img, p);
  return l.x >= 0 && l.x <= img.width && l.y >= 0 && l.y <= img.height;
}

/** The four page-space corners, in order TL, TR, BR, BL of the *unflipped*
 * box — enough to draw a selection outline and hang handles off. */
export function imageCorners(img: Placed): Point[] {
  const c = imageCenter(img);
  const hw = img.width / 2;
  const hh = img.height / 2;
  const rad = (img.rotation * Math.PI) / 180;
  return [
    [-hw, -hh],
    [hw, -hh],
    [hw, hh],
    [-hw, hh],
  ].map(([lx, ly]) => {
    const fx = img.flipped ? -lx : lx;
    return {
      x: c.x + fx * Math.cos(rad) - ly * Math.sin(rad),
      y: c.y + fx * Math.sin(rad) + ly * Math.cos(rad),
    };
  });
}

/** Topmost image under a point, or null. Locked images are skipped: the lock
 * exists so writing over a photo can't grab it, which means it must not even
 * be selectable. */
export function imageAt<T extends Placed & { locked: boolean }>(
  images: T[],
  p: Point
): T | null {
  for (let i = images.length - 1; i >= 0; i--) {
    const img = images[i];
    if (img.locked) continue;
    if (hitTestImage(img, p)) return img;
  }
  return null;
}

export function moveImage(img: ImageBox, dx: number, dy: number): ImageBox {
  return { ...img, x: img.x + dx, y: img.y + dy };
}

/** Resize so the dragged corner follows the pointer, anchored on the centre.
 *
 * Scaling about the centre (rather than the opposite corner) is what keeps this
 * predictable once an image is rotated or mirrored: the handle stays under the
 * finger at any angle, and the aspect ratio is preserved for free.
 */
export function resizeImageToPoint(img: ImageBox, p: Point): ImageBox {
  const c = imageCenter(img);
  const cornerDist = Math.hypot(img.width / 2, img.height / 2);
  if (cornerDist === 0) return img;
  const factor = Math.hypot(p.x - c.x, p.y - c.y) / cornerDist;
  // Clamp on the smaller side so neither dimension can cross the floor.
  const minFactor = MIN_IMAGE_SIZE / Math.min(img.width, img.height);
  const applied = Math.max(factor, minFactor);
  const width = img.width * applied;
  const height = img.height * applied;
  return { x: c.x - width / 2, y: c.y - height / 2, width, height };
}

const MIME_EXT: Record<string, string> = {
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/webp': 'webp',
  'image/gif': 'gif',
  // Accepted, then transcoded to JPEG by the server before it is stored. Paper
  // is a tablet feature and this is what an iPad's photo library hands over, so
  // refusing it here would refuse the common case.
  'image/heic': 'heic',
  'image/heif': 'heif',
};

/** Filename to upload a pasted blob under, or null if we don't store that type.
 * Clipboard data has no name of its own, and the server decides what it will
 * accept from the extension — so the two lists have to agree. */
export function pastedFilename(
  mimeType: string | undefined | null
): string | null {
  const ext = MIME_EXT[(mimeType ?? '').toLowerCase()];
  return ext ? `pasted.${ext}` : null;
}
