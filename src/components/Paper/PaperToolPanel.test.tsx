// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import {
  PANEL_PLACEMENT_KEY,
  parsePanelPlacement,
  type StrokeTool,
} from '@/lib/paper';
import { PaperToolPanel } from './PaperToolPanel';

const BOUNDS = { width: 1000, height: 800 };

function renderPanel(over: Partial<Parameters<typeof PaperToolPanel>[0]> = {}) {
  const props = {
    tool: 'pen' as StrokeTool,
    onToolChange: vi.fn(),
    sizeIndex: 1,
    onSizeIndexChange: vi.fn(),
    canUndo: false,
    canRedo: false,
    onUndo: vi.fn(),
    onRedo: vi.fn(),
    bounds: BOUNDS,
    ...over,
  };
  return { props, ...render(<PaperToolPanel {...props} />) };
}

const panel = () => screen.getByRole('toolbar', { name: 'Drawing tools' });

beforeEach(() => {
  localStorage.clear();
});

describe('PaperToolPanel', () => {
  it('carries the drawing tools and widths', () => {
    renderPanel();
    for (const label of ['Pen', 'Highlighter', 'Eraser', 'Undo', 'Redo']) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy();
    }
    expect(screen.getByRole('button', { name: 'Medium pen' })).toBeTruthy();
  });

  it('reports the selected tool and width as pressed', () => {
    renderPanel({ tool: 'highlighter', sizeIndex: 2 });
    expect(
      screen
        .getByRole('button', { name: 'Highlighter' })
        .getAttribute('aria-pressed')
    ).toBe('true');
    expect(
      screen
        .getByRole('button', { name: 'Large highlighter' })
        .getAttribute('aria-pressed')
    ).toBe('true');
  });

  it('changes tool and width through its callbacks', () => {
    const { props } = renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Eraser' }));
    expect(props.onToolChange).toHaveBeenCalledWith('eraser');
    fireEvent.click(screen.getByRole('button', { name: 'Large pen' }));
    expect(props.onSizeIndexChange).toHaveBeenCalledWith(2);
  });

  it('never shows the save status — that is what made the old bar jitter', () => {
    renderPanel();
    for (const text of ['Saving…', 'Saved', 'Unsaved']) {
      expect(screen.queryByText(text)).toBeNull();
    }
  });

  it('holds the same number of controls whatever the tool, so it cannot resize', () => {
    const { unmount } = renderPanel({ tool: 'pen' });
    const withPen = screen.getAllByRole('button').length;
    unmount();
    renderPanel({ tool: 'highlighter' });
    expect(screen.getAllByRole('button').length).toBe(withPen);
    expect(withPen).toBe(
      1 /* drag handle */ + 3 /* tools */ + 3 /* widths */ + 2 /* undo/redo */
    );
  });

  it('gives every control a touch-sized target', () => {
    renderPanel();
    for (const button of screen.getAllByRole('button')) {
      expect(button.className).toContain('min-h-[44px]');
      expect(button.className).toContain('min-w-[44px]');
    }
  });

  it('lies vertically when docked to a side and horizontally on top', () => {
    localStorage.setItem(
      PANEL_PLACEMENT_KEY,
      JSON.stringify({ edge: 'right', offset: 0.5 })
    );
    const { unmount } = renderPanel();
    expect(panel().getAttribute('aria-orientation')).toBe('vertical');
    unmount();

    localStorage.setItem(
      PANEL_PLACEMENT_KEY,
      JSON.stringify({ edge: 'bottom', offset: 0.5 })
    );
    renderPanel();
    expect(panel().getAttribute('aria-orientation')).toBe('horizontal');
  });

  it('starts against the left edge when nothing has been stored', () => {
    renderPanel();
    expect(panel().getAttribute('aria-orientation')).toBe('vertical');
    expect(panel().style.left).toBe('12px');
  });

  it('snaps to the nearest edge on release and remembers it', () => {
    renderPanel();
    const handle = screen.getByRole('button', { name: 'Move tool panel' });
    // jsdom reports a zero-sized panel, so the drop point is the panel origin.
    fireEvent.pointerDown(handle, { pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(handle, { pointerId: 1, clientX: 900, clientY: 400 });
    fireEvent.pointerUp(handle, { pointerId: 1, clientX: 900, clientY: 400 });

    // Dropped at (800, 300) of a 1000x800 area: the right edge is nearest.
    expect(panel().getAttribute('aria-orientation')).toBe('vertical');
    expect(
      parsePanelPlacement(localStorage.getItem(PANEL_PLACEMENT_KEY))
    ).toEqual({ edge: 'right', offset: 0.375 });
  });

  it('swallows the drag so it can never reach the canvas underneath', () => {
    const onParentPointerDown = vi.fn();
    render(
      <div onPointerDown={onParentPointerDown}>
        <PaperToolPanel
          tool="pen"
          onToolChange={vi.fn()}
          sizeIndex={1}
          onSizeIndexChange={vi.fn()}
          canUndo={false}
          canRedo={false}
          onUndo={vi.fn()}
          onRedo={vi.fn()}
          bounds={BOUNDS}
        />
      </div>
    );
    const handle = screen.getByRole('button', { name: 'Move tool panel' });
    const event = new PointerEvent('pointerdown', {
      pointerId: 1,
      bubbles: true,
      cancelable: true,
    });
    fireEvent(handle, event);
    expect(onParentPointerDown).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(true);
  });
});
