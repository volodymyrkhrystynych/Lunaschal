import { useEffect, useRef, useState } from 'react';
import {
  loadPanelPlacement,
  panelOrientation,
  panelPosition,
  savePanelPlacement,
  sizeDotPx,
  snapPlacement,
  TOOL_SIZES,
  type PanelPlacement,
  type Size,
  type StrokeTool,
} from '@/lib/paper';

const TOOL_META: { id: StrokeTool; label: string; icon: string }[] = [
  { id: 'pen', label: 'Pen', icon: '🖊' },
  { id: 'highlighter', label: 'Highlighter', icon: '🖍' },
  { id: 'eraser', label: 'Eraser', icon: '⌫' },
];
const SIZE_LABELS = ['Small', 'Medium', 'Large'];

interface PaperToolPanelProps {
  tool: StrokeTool;
  onToolChange: (tool: StrokeTool) => void;
  /** Index into TOOL_SIZES[tool] — each tool remembers its own width. */
  sizeIndex: number;
  onSizeIndexChange: (index: number) => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  /** The drawing area the panel floats over, in CSS pixels. */
  bounds: Size;
}

/** The drawing tools, as a panel the user drags around the page and drops
 * against an edge.
 *
 * It exists because the tools used to live in the static top bar next to the
 * autosave indicator: every save popped "Saving…" in and out, the bar reflowed,
 * and the buttons moved out from under a stylus that was aiming for them. So
 * two rules hold here — the panel carries no transient text at all (the save
 * status stays in the top bar, in a fixed-width slot), and every control it does
 * carry is a fixed-size square. Its dimensions depend only on its orientation.
 */
export function PaperToolPanel({
  tool,
  onToolChange,
  sizeIndex,
  onSizeIndexChange,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
  bounds,
}: PaperToolPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [placement, setPlacement] =
    useState<PanelPlacement>(loadPanelPlacement);
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
    savePanelPlacement(next);
    setDragPos(null);
  };

  const square =
    'w-11 h-11 min-w-[44px] min-h-[44px] rounded-lg flex items-center justify-center transition-colors disabled:opacity-30';
  const idle = `${square} bg-[var(--color-surface)] hover:bg-white/10`;
  const active = `${square} bg-[var(--color-primary)] text-[var(--color-bg)]`;

  return (
    <div
      ref={panelRef}
      className={`absolute z-20 flex items-center gap-1 p-1 rounded-xl border border-white/10 bg-[var(--color-bg)]/90 shadow-lg backdrop-blur select-none ${
        orientation === 'vertical' ? 'flex-col' : 'flex-row'
      }`}
      style={{ left: pos.left, top: pos.top, touchAction: 'none' }}
      role="toolbar"
      aria-label="Drawing tools"
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

      {TOOL_META.map(t => (
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

      <div
        className={
          orientation === 'vertical'
            ? 'h-px w-8 my-0.5 bg-white/10'
            : 'w-px h-8 mx-0.5 bg-white/10'
        }
      />

      {/* Width for the active tool. Always three buttons, whatever the tool, so
       * switching tools can't resize the panel. */}
      {TOOL_SIZES[tool].map((px, i) => (
        <button
          key={i}
          onClick={() => onSizeIndexChange(i)}
          className={sizeIndex === i ? active : idle}
          title={`${SIZE_LABELS[i]} ${tool}`}
          aria-label={`${SIZE_LABELS[i]} ${tool}`}
          aria-pressed={sizeIndex === i}
        >
          <span
            className="rounded-full bg-current"
            style={{
              width: `${sizeDotPx(px)}px`,
              height: `${sizeDotPx(px)}px`,
              color: sizeIndex === i ? 'var(--color-bg)' : 'var(--color-text)',
            }}
          />
        </button>
      ))}

      <div
        className={
          orientation === 'vertical'
            ? 'h-px w-8 my-0.5 bg-white/10'
            : 'w-px h-8 mx-0.5 bg-white/10'
        }
      />

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
