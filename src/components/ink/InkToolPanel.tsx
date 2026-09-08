import { useEffect, useRef, useState } from 'react';
import { colorsFor, sizeDotPx, type StrokeTool } from '@/lib/ink';
import {
  loadPanelPlacement,
  panelOrientation,
  panelPosition,
  PANEL_PLACEMENT_KEY,
  savePanelPlacement,
  snapPlacement,
  type PanelPlacement,
} from '@/lib/inkPanel';
import type { Size } from '@/lib/ink';

/** `read` is not a stroke tool — it is the absence of one, offered by surfaces
 * that have something underneath to scroll (the newspaper's column of pages).
 * Paper has nothing to scroll and does not offer it. */
export type PanelTool = StrokeTool | 'read';

export interface PanelToolMeta {
  id: PanelTool;
  label: string;
  icon: string;
}

export const DEFAULT_TOOLS: readonly PanelToolMeta[] = [
  { id: 'pen', label: 'Pen', icon: '🖊' },
  { id: 'highlighter', label: 'Highlighter', icon: '🖍' },
  { id: 'eraser', label: 'Eraser', icon: '⌫' },
];

export const READ_TOOL: PanelToolMeta = {
  id: 'read',
  label: 'Read',
  icon: '✋',
};

const SIZE_LABELS = ['Small', 'Medium', 'Large'];
/** Rendered when a tool has no colour of its own, so the row keeps its width.
 * See the resize note on the component below. */
const COLOR_SLOTS = 4;

export interface InkToolPanelProps {
  tool: PanelTool;
  onToolChange: (tool: PanelTool) => void;
  /** Which tools this surface offers, in the order they are shown. */
  tools?: readonly PanelToolMeta[];
  /** Selectable widths for the active tool, in that surface's own units. */
  sizes: readonly number[];
  /** Index into `sizes` — each tool remembers its own width. */
  sizeIndex: number;
  onSizeIndexChange: (index: number) => void;
  /** The active tool's colour, and where a change goes. Surfaces that predate
   * the picker can leave these out. */
  color?: string;
  onColorChange?: (color: string) => void;
  /** The surface's units per CSS pixel, for the width preview dots. */
  sizeDotUnitsPerPx?: number;
  sizeDotMinPx?: number;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  /** The drawing area the panel floats over, in CSS pixels. */
  bounds: Size;
  /** Where this surface's placement is remembered. Separate keys on purpose:
   * where the panel belongs over an A4 sheet is not where it belongs over a
   * scrolling broadsheet. */
  storageKey?: string;
  label?: string;
}

/** The drawing tools, as a panel the user drags around the page and drops
 * against an edge. Shared by every drawing surface — the Paper editor, the
 * Study desk's embedded page, and the newspaper reader.
 *
 * It exists because the tools used to live in the static top bar next to the
 * autosave indicator: every save popped "Saving…" in and out, the bar reflowed,
 * and the buttons moved out from under a stylus that was aiming for them. So
 * two rules hold here — the panel carries no transient text at all (the save
 * status stays in the top bar, in a fixed-width slot), and every control it does
 * carry is a fixed-size square. Its dimensions depend only on its orientation.
 *
 * That second rule is why the width row is always three buttons and the colour
 * row always four, whatever the tool: the eraser lays down no ink and has no
 * colour, but hiding its swatches would resize the panel on a tool switch,
 * which is the exact failure the fixed-size rule exists to prevent. They render
 * disabled instead.
 */
export function InkToolPanel({
  tool,
  onToolChange,
  tools = DEFAULT_TOOLS,
  sizes,
  sizeIndex,
  onSizeIndexChange,
  color,
  onColorChange,
  sizeDotUnitsPerPx,
  sizeDotMinPx,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
  bounds,
  storageKey = PANEL_PLACEMENT_KEY,
  label = 'Drawing tools',
}: InkToolPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [placement, setPlacement] = useState<PanelPlacement>(() =>
    loadPanelPlacement(storageKey)
  );
  const [panelSize, setPanelSize] = useState<Size>({ width: 0, height: 0 });
  // Free position while a drag is in progress; null when docked.
  const [dragPos, setDragPos] = useState<{ left: number; top: number } | null>(
    null
  );
  const dragRef = useRef<{
    pointerId: number;
    /** Grab point, as an offset inside the panel. */
    dx: number;
    dy: number;
  } | null>(null);

  const orientation = panelOrientation(placement.edge);

  // Its own size is needed to keep it fully on screen, and it changes when the
  // panel re-orients between horizontal and vertical.
  useEffect(() => {
    const el = panelRef.current;
    if (!el) return;
    const measure = () =>
      setPanelSize({ width: el.offsetWidth, height: el.offsetHeight });
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [orientation]);

  const docked = panelPosition(placement, bounds, panelSize);
  const pos = dragPos ?? docked;

  /** Panel coordinates are relative to the drawing area it is positioned in. */
  const areaRect = () =>
    panelRef.current?.offsetParent?.getBoundingClientRect() ?? null;

  const onHandleDown = (e: React.PointerEvent<HTMLDivElement>) => {
    // The canvas is a sibling below, but stop this anyway: a drag must never
    // read as ink, on any future layout.
    e.preventDefault();
    e.stopPropagation();
    const rect = panelRef.current?.getBoundingClientRect();
    if (!rect) return;
    dragRef.current = {
      pointerId: e.pointerId,
      dx: e.clientX - rect.left,
      dy: e.clientY - rect.top,
    };
    setDragPos({ left: pos.left, top: pos.top });
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* jsdom and some older browsers have no pointer capture */
    }
  };

  const onHandleMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d || d.pointerId !== e.pointerId) return;
    e.preventDefault();
    const area = areaRect();
    setDragPos({
      left: e.clientX - (area?.left ?? 0) - d.dx,
      top: e.clientY - (area?.top ?? 0) - d.dy,
    });
  };

  const onHandleUp = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d || d.pointerId !== e.pointerId) return;
    dragRef.current = null;
    const dropped = dragPos ?? pos;
    const next = snapPlacement(
      {
        x: dropped.left + panelSize.width / 2,
        y: dropped.top + panelSize.height / 2,
      },
      bounds
    );
    setPlacement(next);
    savePanelPlacement(next, storageKey);
    setDragPos(null);
  };

  const square =
    'w-11 h-11 min-w-[44px] min-h-[44px] rounded-lg flex items-center justify-center transition-colors disabled:opacity-30';
  const idle = `${square} bg-[var(--color-surface)] hover:bg-white/10`;
  const active = `${square} bg-[var(--color-primary)] text-[var(--color-bg)]`;
  const divider =
    orientation === 'vertical'
      ? 'h-px w-8 my-0.5 bg-white/10'
      : 'w-px h-8 mx-0.5 bg-white/10';

  const swatches = tool === 'read' ? [] : colorsFor(tool);

  return (
    <div
      ref={panelRef}
      className={`absolute z-20 flex items-center gap-1 p-1 rounded-xl border border-white/10 bg-[var(--color-bg)]/90 shadow-lg backdrop-blur select-none ${
        orientation === 'vertical' ? 'flex-col' : 'flex-row'
      }`}
      style={{ left: pos.left, top: pos.top, touchAction: 'none' }}
      role="toolbar"
      aria-label={label}
      aria-orientation={orientation}
    >
      {/* Drag handle. A full 44px target: this is grabbed with a finger. */}
      <div
        onPointerDown={onHandleDown}
        onPointerMove={onHandleMove}
        onPointerUp={onHandleUp}
        onPointerCancel={onHandleUp}
        className={`${square} cursor-grab active:cursor-grabbing text-lg opacity-50 hover:opacity-90`}
        style={{ touchAction: 'none' }}
        role="button"
        aria-label="Move tool panel"
        title="Drag to move — snaps to the nearest edge"
      >
        {orientation === 'vertical' ? '⋯' : '⋮'}
      </div>

      {tools.map(t => (
        <button
          key={t.id}
          onClick={() => onToolChange(t.id)}
          className={tool === t.id ? active : idle}
          title={t.label}
          aria-label={t.label}
          aria-pressed={tool === t.id}
        >
          {t.icon}
        </button>
      ))}

      <div className={divider} />

      {/* Width for the active tool. Always three buttons, whatever the tool, so
       * switching tools can't resize the panel. */}
      {sizes.map((px, i) => (
        <button
          key={i}
          onClick={() => onSizeIndexChange(i)}
          disabled={tool === 'read'}
          className={sizeIndex === i ? active : idle}
          title={`${SIZE_LABELS[i] ?? 'Size'} ${tool}`}
          aria-label={`${SIZE_LABELS[i] ?? 'Size'} ${tool}`}
          aria-pressed={sizeIndex === i}
        >
          <span
            className="rounded-full bg-current"
            style={{
              width: `${sizeDotPx(px, sizeDotUnitsPerPx, sizeDotMinPx)}px`,
              height: `${sizeDotPx(px, sizeDotUnitsPerPx, sizeDotMinPx)}px`,
              color: sizeIndex === i ? 'var(--color-bg)' : 'var(--color-text)',
            }}
          />
        </button>
      ))}

      {onColorChange && (
        <>
          <div className={divider} />
          {Array.from({ length: COLOR_SLOTS }, (_, i) => {
            const value = swatches[i];
            if (!value) {
              return (
                <button
                  key={i}
                  disabled
                  aria-hidden="true"
                  tabIndex={-1}
                  className={idle}
                />
              );
            }
            const chosen = color === value;
            return (
              <button
                key={i}
                onClick={() => onColorChange(value)}
                className={chosen ? active : idle}
                title={value}
                aria-label={`${tool} colour ${value}`}
                aria-pressed={chosen}
              >
                <span
                  className="rounded-full border border-black/20"
                  style={{
                    width: chosen ? '20px' : '16px',
                    height: chosen ? '20px' : '16px',
                    background: value,
                  }}
                />
              </button>
            );
          })}
        </>
      )}

      <div className={divider} />

      <button
        onClick={onUndo}
        disabled={!canUndo}
        className={idle}
        title="Undo"
        aria-label="Undo"
      >
        ↶
      </button>
      <button
        onClick={onRedo}
        disabled={!canRedo}
        className={idle}
        title="Redo"
        aria-label="Redo"
      >
        ↷
      </button>
    </div>
  );
}
