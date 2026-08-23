import { describe, it, expect } from 'vitest';
import { PAGE_HEIGHT, PAGE_WIDTH } from './paper';
import {
  fitPastedImage,
  hitTestImage,
  imageAt,
  imageCenter,
  imageCorners,
  MIN_IMAGE_SIZE,
  moveImage,
  normalizeRotation,
  pastedFilename,
  ROTATION_STEP,
  resizeImageToPoint,
  rotateBy,
  snapRotation,
  toImageLocal,
} from './paperImages';

const img = (over: Partial<Parameters<typeof hitTestImage>[0]> = {}) => ({
  x: 100,
  y: 100,
  width: 400,
  height: 200,
  rotation: 0,
  flipped: false,
  ...over,
});

const near = (a: number, b: number) => expect(a).toBeCloseTo(b, 6);

describe('rotation', () => {
  it('normalises onto [0, 360)', () => {
    expect(normalizeRotation(0)).toBe(0);
    expect(normalizeRotation(360)).toBe(0);
    expect(normalizeRotation(-45)).toBe(315);
    expect(normalizeRotation(450)).toBe(90);
    expect(normalizeRotation(NaN)).toBe(0);
  });

  it('steps forward and backward without drifting past a full turn', () => {
    let deg = 0;
    for (let i = 0; i < 4; i++) deg = rotateBy(deg, ROTATION_STEP);
    expect(deg).toBe(0); // four quarter turns is exactly one turn
    expect(rotateBy(0, -ROTATION_STEP)).toBe(270);
  });

  it('snaps a hand-set angle onto the quarter-turn grid', () => {
    expect(ROTATION_STEP).toBe(90);
    expect(snapRotation(80)).toBe(90);
    expect(snapRotation(46)).toBe(90); // 45 is the midpoint between 0 and 90
    expect(snapRotation(44)).toBe(0);
    expect(snapRotation(350)).toBe(0);
    // An angle stored from an earlier, finer grid gets pulled back on.
    expect(snapRotation(135)).toBe(180);
  });
});

describe('fitPastedImage', () => {
  it('centres the image on the page', () => {
    const box = fitPastedImage(800, 600);
    const c = imageCenter(box);
    near(c.x, PAGE_WIDTH / 2);
    near(c.y, PAGE_HEIGHT / 2);
  });

  it('preserves the aspect ratio', () => {
    const box = fitPastedImage(1600, 400);
    near(box.width / box.height, 4);
  });

  it('lands a tiny icon and a huge photo at a comparable size', () => {
    // Pixel dimensions say nothing about how big it should be on an A4 page;
    // both should arrive workable rather than invisible or off the sheet.
    const icon = fitPastedImage(64, 64);
    const photo = fitPastedImage(4000, 4000);
    near(icon.width, photo.width);
    expect(icon.width).toBeLessThanOrEqual(PAGE_WIDTH);
  });

  it('keeps a wildly lopsided image on the page', () => {
    const box = fitPastedImage(10000, 50);
    expect(box.width).toBeLessThanOrEqual(PAGE_WIDTH);
    expect(box.height).toBeLessThanOrEqual(PAGE_HEIGHT);
  });

  it('survives a zero-sized natural size', () => {
    const box = fitPastedImage(0, 0);
    expect(box.width).toBeGreaterThan(0);
    expect(box.height).toBeGreaterThan(0);
  });
});

describe('hit testing', () => {
  it('accepts the centre and rejects a point outside', () => {
    expect(hitTestImage(img(), { x: 300, y: 200 })).toBe(true);
    expect(hitTestImage(img(), { x: 90, y: 200 })).toBe(false);
  });

  it('follows the image when it is rotated', () => {
    const rotated = img({ rotation: 90 });
    // A 400x200 box rotated a quarter turn covers 200x400 about the same centre:
    // a point 150 above the centre is inside now, and was not before.
    const c = imageCenter(rotated);
    expect(hitTestImage(rotated, { x: c.x, y: c.y - 150 })).toBe(true);
    expect(hitTestImage(img(), { x: c.x, y: c.y - 150 })).toBe(false);
  });

  it('is unchanged by mirroring, which covers the same area', () => {
    const p = { x: 180, y: 150 };
    expect(hitTestImage(img({ flipped: true }), p)).toBe(
      hitTestImage(img(), p)
    );
  });

  it('maps a corner into local space', () => {
    const local = toImageLocal(img(), { x: 100, y: 100 });
    near(local.x, 0);
    near(local.y, 0);
  });

  it('mirrors the local x when flipped', () => {
    // The top-left page corner is the image's top-*right* once mirrored.
    const local = toImageLocal(img({ flipped: true }), { x: 100, y: 100 });
    near(local.x, 400);
    near(local.y, 0);
  });
});

describe('imageCorners', () => {
  it('returns the plain box when unrotated', () => {
    const [tl, tr, br, bl] = imageCorners(img());
    near(tl.x, 100);
    near(tl.y, 100);
    near(tr.x, 500);
    near(br.y, 300);
    near(bl.x, 100);
  });

  it('rotates about the centre, leaving it fixed', () => {
    const before = imageCenter(img());
    // Deliberately off-grid: the maths must not assume a quarter turn.
    const corners = imageCorners(img({ rotation: 45 }));
    const avg = corners.reduce(
      (a, p) => ({ x: a.x + p.x / 4, y: a.y + p.y / 4 }),
      { x: 0, y: 0 }
    );
    near(avg.x, before.x);
    near(avg.y, before.y);
  });
});

describe('imageAt', () => {
  const a = { ...img(), id: 'a', locked: false, position: 0 };
  const b = { ...img(), id: 'b', locked: false, position: 1 };

  it('picks the topmost of two overlapping images', () => {
    expect(imageAt([a, b], { x: 300, y: 200 })?.id).toBe('b');
  });

  it('skips a locked image entirely', () => {
    // The whole point of the lock: writing over a photo must not grab it.
    expect(imageAt([a, { ...b, locked: true }], { x: 300, y: 200 })?.id).toBe(
      'a'
    );
  });

  it('returns null when everything under the point is locked', () => {
    expect(imageAt([{ ...a, locked: true }], { x: 300, y: 200 })).toBeNull();
  });

  it('returns null on empty space', () => {
    expect(imageAt([a, b], { x: 5, y: 5 })).toBeNull();
  });
});

describe('move and resize', () => {
  it('moves by a delta', () => {
    const moved = moveImage(img(), 10, -20);
    expect(moved.x).toBe(110);
    expect(moved.y).toBe(80);
  });

  it('resizes toward the pointer, keeping the centre fixed', () => {
    const before = img();
    const c = imageCenter(before);
    // Original corner sits at distance hypot(200,100) from the centre; aim at
    // twice that and the box should double.
    const corner = Math.hypot(200, 100);
    const after = resizeImageToPoint(before, { x: c.x + corner * 2, y: c.y });
    near(after.width, 800);
    near(after.height, 400);
    near(imageCenter(after).x, c.x);
    near(imageCenter(after).y, c.y);
  });

  it('preserves the aspect ratio', () => {
    const after = resizeImageToPoint(img(), { x: 120, y: 120 });
    near(after.width / after.height, 2);
  });

  it('refuses to shrink below the floor', () => {
    // Dragging onto the centre would otherwise produce a zero-size image, which
    // can never be selected again.
    const c = imageCenter(img());
    const after = resizeImageToPoint(img(), c);
    expect(Math.min(after.width, after.height)).toBeGreaterThanOrEqual(
      MIN_IMAGE_SIZE
    );
  });
});

describe('pastedFilename', () => {
  it('names the common clipboard types', () => {
    expect(pastedFilename('image/png')).toBe('pasted.png');
    expect(pastedFilename('image/jpeg')).toBe('pasted.jpg');
    expect(pastedFilename('IMAGE/WEBP')).toBe('pasted.webp');
  });

  it('names the HEIC an iPad hands over, which the server transcodes', () => {
    // Refusing it here would refuse the common case: paper is a tablet
    // feature, and the tablet's photo library is where its pictures come from.
    expect(pastedFilename('image/heic')).toBe('pasted.heic');
    expect(pastedFilename('image/heif')).toBe('pasted.heif');
  });

  it('refuses a type the server would reject anyway', () => {
    // Better to say so in the UI than to upload and take a 400.
    expect(pastedFilename('image/svg+xml')).toBeNull();
    expect(pastedFilename('text/plain')).toBeNull();
    expect(pastedFilename(undefined)).toBeNull();
  });
});
