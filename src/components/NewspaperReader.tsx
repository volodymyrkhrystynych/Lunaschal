import { useEffect, useRef, useState } from 'react';
import * as pdfjs from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import {
  api,
  ApiError,
  type NewspaperIssue,
  type NewspaperMarkup,
} from '../hooks/api';
import {
  eraseStroke,
  simplifyStroke,
  strokeColor,
  type Stroke,
} from '@/lib/ink';
import {
  canRedo,
  canUndo,
  commitOn,
  countPoints,
  countStrokes,
  emptyMarkup,
  fromWire,
  inkSpaceFor,
  MAX_POINTS,
  MAX_POINTS_PER_STROKE,
  MAX_STROKES,
  NEWSPAPER_PALETTE,
  NEWSPAPER_TOOL_SIZES,
  redoLast,
  setStrokesOn,
  strokesOn,
  toInkStroke,
  toWire,
  toWireStroke,
  undoLast,
  type IssueMarkup,
} from '@/lib/newspaperMarkup';
import {
  InkToolPanel,
  READ_TOOL,
  DEFAULT_TOOLS,
  type PanelTool,
} from '@/components/ink/InkToolPanel';
import { HIGHLIGHTER_COLORS, PEN_COLORS } from '@/lib/ink';

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

// Safari alone reports what made a touch, and it is the only browser an Apple
// Pencil reaches us through; elsewhere the field is simply absent.
type StylusTouch = Touch & { touchType?: 'direct' | 'stylus' };

/** Reading is the fourth option here and the default: the Pencil scrolls like a
 * finger until a marking tool is chosen. */
const TOOLS = [READ_TOOL, ...DEFAULT_TOOLS];
const PANEL_KEY = 'lunaschal:newspaperToolPanel';
/** Ink units are thousandths of a page width; the panel's preview dots want
 * CSS pixels. A floor keeps the smallest pen visible as a dot. */
const DOT_UNITS_PER_PX = 0.3;
const DOT_MIN_PX = 5;
/** Points closer than this add nothing at any zoom a page is read at. */
const MIN_POINT_DISTANCE = 1.5;

function Page({
  pdf,
  number,
  tool,
  size,
  color,
  strokes,
  onCommit,
  onErase,
}: {
  pdf: pdfjs.PDFDocumentProxy;
  number: number;
  /** null in Read mode: nothing marks, and every touch belongs to the browser. */
  tool: PanelTool;
  size: number;
  color: string;
  /** This page's strokes, in stored (normalised) space. */
  strokes: Stroke[];
  onCommit: (page: number, stroke: Stroke) => void;
  onErase: (page: number, survivors: Stroke[]) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const drawing = useRef<Stroke | null>(null);
  const [preview, setPreview] = useState<Stroke | null>(null);
  /** While the eraser is down, what the page would look like if it lifted now. */
  const [erasing, setErasing] = useState<Stroke[] | null>(null);
  const [ratio, setRatio] = useState(1.3);
  const [visible, setVisible] = useState(false);
  const [width, setWidth] = useState(0);
  const [error, setError] = useState('');

  useEffect(() => {
    const element = container.current!;
    const observer = new IntersectionObserver(
      entries => setVisible(entries[0].isIntersecting),
      { rootMargin: '600px' }
    );
    const resize = new ResizeObserver(entries =>
      setWidth(entries[0].contentRect.width)
    );
    observer.observe(element);
    resize.observe(element);
    return () => {
      observer.disconnect();
      resize.disconnect();
    };
  }, []);

  useEffect(() => {
    if (!visible || !width) return;
    let cancelled = false;
    let task: pdfjs.RenderTask | undefined;
    const element = canvas.current!;
    const render = async () => {
      const page = await pdf.getPage(number);
      if (cancelled) return;
      const base = page.getViewport({ scale: 1 });
      setRatio(base.height / base.width);
      // Cap canvas pixels, particularly on high-DPI iPads with large broadsheets.
      const scale = Math.min(window.devicePixelRatio || 1, 2, 2048 / width);
      const viewport = page.getViewport({
        scale: (width * scale) / base.width,
      });
      element.width = Math.ceil(viewport.width);
      element.height = Math.ceil(viewport.height);
      task = page.render({
        canvasContext: element.getContext('2d')!,
        viewport,
      });
      await task.promise;
    };
    void render().catch(e => {
      if (!cancelled) setError(String(e));
    });
    return () => {
      cancelled = true;
      task?.cancel();
      element.width = 0;
      element.height = 0;
    };
  }, [pdf, number, visible, width]);

  // The Pencil must never scroll while a marking tool is selected, and iPadOS
  // gives only one way to enforce that: cancel the touch stream itself. Neither
  // `touch-action` (WebKit ignores it on an <svg>) nor preventDefault on
  // pointerdown stops a WebKit scroll, and once the scroll starts the pen
  // pointer is cancelled mid-stroke — which is exactly what "it scrolls instead
  // of writing" was. Fingers are left entirely alone so they keep native
  // momentum scrolling and pinch-zoom, except while a stroke is in progress,
  // where a resting palm would otherwise drag the page out from under the nib.
  // Must be a native non-passive listener: React attaches touchmove passively,
  // so an onTouchMove prop cannot preventDefault.
  const marking = tool !== 'read';
  useEffect(() => {
    const element = container.current!;
    const onTouchMove = (event: TouchEvent) => {
      const touches = Array.from(event.touches) as StylusTouch[];
      const stylus =
        touches.length > 0 && touches.every(t => t.touchType === 'stylus');
      if (drawing.current || (marking && stylus)) event.preventDefault();
    };
    element.addEventListener('touchmove', onTouchMove, { passive: false });
    return () => element.removeEventListener('touchmove', onTouchMove);
  }, [marking]);

  const space = inkSpaceFor(ratio);

  /** A pointer position in this page's ink space. Read per axis from the live
   * rect, which is what makes the ratio cancel on the way back out to storage —
   * see toWireStroke. */
  function point(event: React.PointerEvent<SVGSVGElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const fx = rect.width ? (event.clientX - rect.left) / rect.width : 0;
    const fy = rect.height ? (event.clientY - rect.top) / rect.height : 0;
    return {
      x: Math.max(0, Math.min(1, fx)) * space.width,
      y: Math.max(0, Math.min(1, fy)) * space.height,
      pressure: event.pressure > 0 ? event.pressure : 0.5,
    };
  }

  const inkStrokes = (erasing ?? strokes).map(s => toInkStroke(s, ratio));
  const shown = preview ? [...inkStrokes, preview] : inkStrokes;

  const endStroke = () => {
    const stroke = drawing.current;
    drawing.current = null;
    setPreview(null);
    setErasing(null);
    return stroke;
  };

  return (
    <div
      ref={container}
      className="relative w-full bg-white mb-3"
      style={{ aspectRatio: `1 / ${ratio}` }}
      aria-label={`Page ${number}`}
    >
      <canvas ref={canvas} className="absolute inset-0 w-full h-full" />
      {error && (
        <p role="alert" className="absolute top-0 text-red-700 bg-white">
          {error}
        </p>
      )}
      <svg
        ref={svgRef}
        className="absolute inset-0 w-full h-full"
        viewBox={`0 0 ${space.width} ${space.height}`}
        // Never 'none': a finger has to keep scrolling the reader in every
        // tool, and the Pencil is held off by the touchmove listener above.
        style={{ touchAction: 'pan-y pinch-zoom' }}
        onPointerDown={event => {
          // Fingers only scroll; Pencil (or a mouse, for testing) only marks.
          if (
            !marking ||
            !event.isPrimary ||
            event.pointerType === 'touch' ||
            drawing.current
          )
            return;
          event.preventDefault();
          event.currentTarget.setPointerCapture(event.pointerId);
          drawing.current = {
            tool: tool as Stroke['tool'],
            size,
            points: [point(event)],
            ...(tool === 'eraser' ? {} : { color }),
          };
          setPreview(tool === 'eraser' ? null : { ...drawing.current });
        }}
        onPointerMove={event => {
          const stroke = drawing.current;
          if (
            !stroke ||
            !event.currentTarget.hasPointerCapture(event.pointerId)
          )
            return;
          if (stroke.points.length < MAX_POINTS_PER_STROKE) {
            stroke.points.push(point(event));
          }
          if (stroke.tool === 'eraser') {
            // The eraser changes many strokes at once, so show the result of
            // lifting now rather than the eraser's own path.
            const survivors = eraseStroke(
              {
                strokes: strokes.map(s => toInkStroke(s, ratio)),
                history: [],
                redo: [],
              },
              stroke
            ).strokes;
            setErasing(survivors.map(s => toWireStroke(s, ratio)));
          } else {
            setPreview({ ...stroke });
          }
        }}
        onPointerUp={event => {
          if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
          const survivors = erasing;
          const stroke = endStroke();
          event.currentTarget.releasePointerCapture(event.pointerId);
          if (!stroke) return;
          if (stroke.tool === 'eraser') {
            // A scrub that touched nothing is not an edit, and should not cost
            // an undo step.
            if (survivors && survivors.length !== strokes.length) {
              onErase(number, survivors);
            }
            return;
          }
          onCommit(
            number,
            toWireStroke(simplifyStroke(stroke, MIN_POINT_DISTANCE), ratio)
          );
        }}
        // Discarded rather than committed, unlike the Paper editor: a cancel
        // here means iPadOS took the pointer to scroll with, and half a stray
        // line dragged across a photograph is worse than no line at all.
        onPointerCancel={endStroke}
      >
        {shown.map((stroke, i) => (
          <polyline
            key={i}
            points={stroke.points.map(p => `${p.x},${p.y}`).join(' ')}
            fill="none"
            stroke={strokeColor(stroke, NEWSPAPER_PALETTE)}
            strokeWidth={stroke.size}
            opacity={
              stroke.tool === 'highlighter'
                ? NEWSPAPER_PALETTE.highlightAlpha
                : 1
            }
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ))}
      </svg>
      <span className="absolute bottom-0 right-1 text-xs text-gray-500 pointer-events-none">
        {number}
      </span>
    </div>
  );
}

/** #rrggbb into the 0..1 triple pdf-lib wants. */
function toRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

export function NewspaperReader({
  issue,
  onClose,
}: {
  issue: NewspaperIssue;
  onClose: () => void;
}) {
  const [pdf, setPdf] = useState<pdfjs.PDFDocumentProxy | null>(null);
  const [markup, setMarkup] = useState<IssueMarkup>(emptyMarkup);
  const [tool, setTool] = useState<PanelTool>('read');
  const [sizeIndex, setSizeIndex] = useState<Record<string, number>>({
    pen: 1,
    highlighter: 1,
    eraser: 1,
  });
  const [color, setColor] = useState<Record<string, string>>({
    // The colour this reader has always drawn in: black would vanish into
    // newsprint.
    pen: PEN_COLORS[1],
    highlighter: HIGHLIGHTER_COLORS[0],
  });
  const [status, setStatus] = useState('Loading…');
  const [ready, setReady] = useState(false);
  const [exporting, setExporting] = useState(false);
  // Whether the server has actually refused the markup — a failed save or a
  // conflicting draft. Not "a save is in flight", which is every other moment
  // while drawing.
  const [unsaved, setUnsaved] = useState(false);
  const [area, setArea] = useState({ width: 0, height: 0 });
  const areaRef = useRef<HTMLDivElement>(null);
  const revision = useRef(0);
  const markupRef = useRef(markup);
  markupRef.current = markup;
  const saving = useRef(false);
  const dirty = useRef(false);
  const conflict = useRef(false);
  const key = `newspaper-markup:${issue.date}`;

  const currentSize =
    tool === 'read'
      ? 0
      : (NEWSPAPER_TOOL_SIZES[tool][sizeIndex[tool] ?? 1] ?? 2);

  // The panel floats over the reading area, so it needs that area's size to
  // stay on screen when the window changes.
  useEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    const measure = () =>
      setArea({ width: el.clientWidth, height: el.clientHeight });
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const draft = (next: IssueMarkup) => ({
    revision: revision.current,
    strokes: toWire(next),
  });

  useEffect(() => {
    const loading = pdfjs.getDocument({ url: issue.pdfUrl });
    let active = true;
    void Promise.all([loading.promise, api.newspapers.markup(issue.date)])
      .then(([document, saved]) => {
        if (!active) return;
        let recovered: NewspaperMarkup | null = null;
        try {
          recovered = JSON.parse(localStorage.getItem(key) || 'null');
        } catch {
          /* No readable draft. */
        }
        if (recovered && Array.isArray(recovered.strokes)) {
          conflict.current = recovered.revision !== saved.revision;
          revision.current = recovered.revision;
          setMarkup(fromWire(recovered.strokes));
          dirty.current = true;
          setUnsaved(conflict.current);
          setStatus(
            conflict.current
              ? 'A local draft conflicts with the server. Export it before reopening on another device.'
              : 'Recovered local markup; saving…'
          );
        } else {
          revision.current = saved.revision;
          setMarkup(fromWire(saved.strokes));
          setStatus('Saved');
        }
        setPdf(document);
        setReady(true);
      })
      .catch(e => {
        if (active) setStatus(e.message);
      });
    return () => {
      active = false;
      void loading.destroy();
    };
  }, [issue.date, issue.pdfUrl, key]);

  async function save() {
    if (!dirty.current || saving.current || conflict.current) return;
    saving.current = true;
    const snapshot = markupRef.current;
    setStatus('Saving…');
    try {
      const result = await api.newspapers.saveMarkup(
        issue.date,
        draft(snapshot)
      );
      const changed = markupRef.current !== snapshot;
      revision.current = result.revision;
      dirty.current = changed;
      if (changed) {
        localStorage.setItem(key, JSON.stringify(draft(markupRef.current)));
      } else {
        localStorage.removeItem(key);
      }
      setUnsaved(false);
      setStatus(changed ? 'Saving…' : 'Saved');
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) conflict.current = true;
      setUnsaved(true);
      setStatus(`Not saved to server: ${(e as Error).message}`);
    } finally {
      saving.current = false;
    }
  }

  useEffect(() => {
    if (!ready) return;
    const timer = window.setInterval(() => {
      void save();
    }, 1500);
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirty.current) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => {
      clearInterval(timer);
      window.removeEventListener('beforeunload', beforeUnload);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  function change(next: IssueMarkup) {
    if (countStrokes(next) > MAX_STROKES || countPoints(next) > MAX_POINTS) {
      setStatus(
        'This issue has reached its markup limit. Export it before adding more marks.'
      );
      return;
    }
    setMarkup(next);
    markupRef.current = next;
    dirty.current = true;
    try {
      localStorage.setItem(key, JSON.stringify(draft(next)));
    } catch {
      setStatus(
        'Local storage is full. Keep this reader open until server save completes.'
      );
    }
  }

  async function exportPdf() {
    if (!pdf) return;
    setExporting(true);
    try {
      const { PDFDocument, rgb, LineCapStyle } = await import('pdf-lib');
      const output = await PDFDocument.load(await pdf.getData());
      for (const wire of toWire(markupRef.current)) {
        const source = await pdf.getPage(wire.page);
        const view = source.getViewport({ scale: 1 });
        const target = output.getPage(wire.page - 1);
        const stroke = strokesOn(fromWire([wire]), wire.page)[0];
        if (!stroke) continue;
        const [r, g, b] = toRgb(strokeColor(stroke, NEWSPAPER_PALETTE));
        const points = stroke.points.map(p => ({
          at: view.convertToPdfPoint(p.x * view.width, p.y * view.height),
          pressure: p.pressure,
        }));
        for (let i = 0; i < points.length; i++) {
          const start = points[Math.max(0, i - 1)];
          const end = points[i];
          // Ink units are thousandths of a page width, which is what makes a
          // stroke the same weight here as it is on screen.
          const weight =
            stroke.tool === 'pen'
              ? stroke.size * (0.35 + 0.65 * end.pressure)
              : stroke.size;
          target.drawLine({
            start: { x: start.at[0], y: start.at[1] },
            end: { x: end.at[0], y: end.at[1] },
            thickness: (view.width * weight) / 1000,
            color: rgb(r, g, b),
            opacity:
              stroke.tool === 'highlighter'
                ? NEWSPAPER_PALETTE.highlightAlpha
                : 1,
            lineCap: LineCapStyle.Round,
          });
        }
      }
      const bytes = await output.save();
      const url = URL.createObjectURL(
        new Blob([new Uint8Array(bytes)], { type: 'application/pdf' })
      );
      const link = document.createElement('a');
      link.href = url;
      link.download = `toronto-star-${issue.date}-annotated.pdf`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (e) {
      setStatus(`Export failed: ${(e as Error).message}`);
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[var(--color-bg)] text-[var(--color-text)]">
      <div className="flex flex-wrap items-center gap-2 p-2 border-b border-white/10">
        <button
          className="p-2"
          onClick={async () => {
            await save();
            if (!dirty.current) onClose();
          }}
        >
          ← Newspapers
        </button>
        <span>Toronto Star · {issue.date}</span>
        <button
          className="p-2"
          disabled={!ready || exporting}
          onClick={() => void exportPdf()}
        >
          {exporting ? 'Exporting…' : 'Export PDF'}
        </button>
        {ready && (
          <button className="p-2" onClick={() => void save()}>
            Save now
          </button>
        )}
        {ready && conflict.current && (
          <button
            className="p-2"
            onClick={async () => {
              if (
                !window.confirm(
                  'Discard this local draft and load the saved server copy? Export your local markup first if you want to keep it.'
                )
              )
                return;
              try {
                const latest = await api.newspapers.markup(issue.date);
                localStorage.removeItem(key);
                revision.current = latest.revision;
                setMarkup(fromWire(latest.strokes));
                dirty.current = false;
                conflict.current = false;
                setUnsaved(false);
                setStatus('Saved');
              } catch (e) {
                setStatus((e as Error).message);
              }
            }}
          >
            Use server copy
          </button>
        )}
        {ready && unsaved && (
          <button
            className="p-2"
            onClick={() => {
              try {
                localStorage.setItem(
                  key,
                  JSON.stringify(draft(markupRef.current))
                );
                onClose();
              } catch {
                setStatus(
                  'Cannot store the draft locally. Save to server or export before closing.'
                );
              }
            }}
          >
            Close with local draft
          </button>
        )}
        <span role="status" className="text-xs min-w-24">
          {status}
        </span>
        {!ready && (
          <button className="p-2" onClick={onClose}>
            Close
          </button>
        )}
      </div>
      <div ref={areaRef} className="relative flex-1 min-h-0">
        <div className="absolute inset-0 overflow-y-auto overscroll-contain">
          {pdf &&
            Array.from({ length: pdf.numPages }, (_, i) => (
              <Page
                key={i}
                pdf={pdf}
                number={i + 1}
                tool={tool}
                size={currentSize}
                color={color[tool] ?? ''}
                strokes={strokesOn(markup, i + 1)}
                onCommit={(page, stroke) =>
                  change(commitOn(markupRef.current, page, stroke))
                }
                onErase={(page, survivors) =>
                  change(setStrokesOn(markupRef.current, page, survivors))
                }
              />
            ))}
        </div>
        {ready && (
          <InkToolPanel
            tool={tool}
            onToolChange={setTool}
            tools={TOOLS}
            sizes={
              tool === 'read'
                ? NEWSPAPER_TOOL_SIZES.pen
                : NEWSPAPER_TOOL_SIZES[tool]
            }
            sizeIndex={sizeIndex[tool] ?? 1}
            onSizeIndexChange={i => setSizeIndex(s => ({ ...s, [tool]: i }))}
            color={color[tool]}
            onColorChange={next => setColor(c => ({ ...c, [tool]: next }))}
            sizeDotUnitsPerPx={DOT_UNITS_PER_PX}
            sizeDotMinPx={DOT_MIN_PX}
            canUndo={canUndo(markup)}
            canRedo={canRedo(markup)}
            onUndo={() => change(undoLast(markupRef.current))}
            onRedo={() => change(redoLast(markupRef.current))}
            bounds={area}
            storageKey={PANEL_KEY}
            label="Markup tools"
          />
        )}
      </div>
    </div>
  );
}
