// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { PaperImageLayer } from './PaperImageLayer';
import type { PageImage } from '@/lib/paperImages';

const image = (over: Partial<PageImage> = {}): PageImage => ({
  id: 'img-1',
  url: '/api/paper/images/img-1/file',
  x: 100,
  y: 100,
  width: 400,
  height: 200,
  rotation: 0,
  flipped: false,
  locked: false,
  position: 0,
  ...over,
});

// The layer maps client coords through its own box, so it needs a real one.
const SCALE = 0.5;
function renderLayer(
  over: Partial<Parameters<typeof PaperImageLayer>[0]> = {}
) {
  // Held as locals (not read back off `props`) so their Mock types survive.
  const onSelect = vi.fn();
  const onPreview = vi.fn();
  const onCommit = vi.fn();
  const view = render(
    <PaperImageLayer
      images={[image()]}
      scale={SCALE}
      selectedId={null}
      onSelect={onSelect}
      onPreview={onPreview}
      onCommit={onCommit}
      {...over}
    />
  );
  const layer = screen.getByTestId('paper-image-layer');
  layer.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 1050, height: 1485 }) as DOMRect;
  return { ...view, layer, onSelect, onPreview, onCommit };
}

// A page point, expressed as the client coords the layer will convert back.
const at = (x: number, y: number) => ({
  clientX: x * SCALE,
  clientY: y * SCALE,
  pointerId: 1,
  bubbles: true,
});

beforeEach(() => {
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
});

describe('selecting', () => {
  it('selects the image under the pointer', () => {
    const { layer, onSelect, onPreview, onCommit } = renderLayer();
    fireEvent.pointerDown(layer, at(300, 200));
    expect(onSelect).toHaveBeenCalledWith('img-1');
  });

  it('deselects when tapping empty page', () => {
    const { layer, onSelect, onPreview, onCommit } = renderLayer();
    fireEvent.pointerDown(layer, at(20, 20));
    expect(onSelect).toHaveBeenCalledWith(null);
  });

  it('will not select a locked image', () => {
    // The lock exists so that writing over a photo cannot grab it. A tap on a
    // locked picture has to read as a tap on empty page.
    const { layer, onSelect, onPreview, onCommit } = renderLayer({
      images: [image({ locked: true })],
    });
    fireEvent.pointerDown(layer, at(300, 200));
    expect(onSelect).toHaveBeenCalledWith(null);
  });
});

describe('dragging', () => {
  it('previews while moving and commits once on release', () => {
    const { layer, onSelect, onPreview, onCommit } = renderLayer({
      selectedId: 'img-1',
    });
    fireEvent.pointerDown(layer, at(300, 200));
    fireEvent.pointerMove(layer, at(350, 260));

    expect(onPreview).toHaveBeenCalledTimes(1);
    const [, previewed] = onPreview.mock.calls[0];
    expect(previewed.x).toBeCloseTo(150, 6);
    expect(previewed.y).toBeCloseTo(160, 6);
    // Nothing is persisted mid-drag — that would be a PATCH per pointer move.
    expect(onCommit).not.toHaveBeenCalled();

    fireEvent.pointerUp(layer, at(350, 260));
    expect(onCommit).toHaveBeenCalledTimes(1);
    expect(onCommit.mock.calls[0][1]).toMatchObject({ x: 150, y: 160 });
  });

  it('does not move anything when the drag started on empty page', () => {
    const { layer, onSelect, onPreview, onCommit } = renderLayer();
    fireEvent.pointerDown(layer, at(20, 20));
    fireEvent.pointerMove(layer, at(400, 400));
    expect(onPreview).not.toHaveBeenCalled();
    expect(onCommit).not.toHaveBeenCalled();
  });

  it('resizes from the corner handle instead of moving', () => {
    const { onPreview } = renderLayer({ selectedId: 'img-1' });
    const handle = screen.getByTestId('paper-image-resize');
    fireEvent.pointerDown(handle, at(500, 300));
    fireEvent.pointerMove(
      screen.getByTestId('paper-image-layer'),
      at(700, 400)
    );

    expect(onPreview).toHaveBeenCalled();
    const [, box] = onPreview.mock.calls.at(-1)!;
    expect(box.width).toBeGreaterThan(400);
    // Aspect ratio survives the resize.
    expect(box.width / box.height).toBeCloseTo(2, 6);
  });
});

describe('selection chrome', () => {
  it('shows an outline and a handle only for the selected image', () => {
    const { rerender } = renderLayer();
    expect(screen.queryByTestId('paper-image-selection')).toBeNull();
    expect(screen.queryByTestId('paper-image-resize')).toBeNull();

    rerender(
      <PaperImageLayer
        images={[image()]}
        scale={SCALE}
        selectedId="img-1"
        onSelect={vi.fn()}
        onPreview={vi.fn()}
        onCommit={vi.fn()}
      />
    );
    expect(screen.getByTestId('paper-image-selection')).toBeTruthy();
    expect(screen.getByTestId('paper-image-resize')).toBeTruthy();
  });

  it('mirrors the outline transform onto the image geometry', () => {
    renderLayer({
      images: [image({ rotation: 45, flipped: true })],
      selectedId: 'img-1',
    });
    const outline = screen.getByTestId('paper-image-selection');
    // Must match what the canvas draws, or the outline lies about the picture.
    expect(outline.style.transform).toContain('rotate(45deg)');
    expect(outline.style.transform).toContain('scaleX(-1)');
  });
});
