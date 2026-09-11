import { useEffect, useMemo, useRef, useState } from 'react';
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
import { InkSurface } from '@/components/ink/InkSurface';
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
  reseed,
  onEdit,
}: {
  pdf: pdfjs.PDFDocumentProxy;
  number: number;
  /** 'read' means nothing marks, and every touch belongs to the browser. */
  tool: PanelTool;
  size: number;
  color: string;
  /** This page's strokes, in stored (normalised) space. */
  strokes: Stroke[];
  /** Bumped when the reader has refused an edit the ink layer already drew, to
   * pull the surface back to what is actually stored. */
  reseed: number;
  onEdit: (page: number, strokes: Stroke[]) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
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

  const space = useMemo(() => inkSpaceFor(ratio), [ratio]);
  // Identity matters: the ink surface re-seeds whenever this array changes, so
  // it must change when the strokes do and not merely when the page re-renders.
  const inked = useMemo(
    () => strokes.map(s => toInkStroke(s, ratio)),
    // `reseed` is deliberately part of this: a refused edit leaves the ink
    // layer holding a stroke the markup does not have, and a fresh identity is
    // what makes it adopt the stored strokes again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [strokes, ratio, reseed]
  );

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
      {/* Mounted only while the page is near the viewport, and torn down with
       * it. An issue runs to hundreds of pages, and while SVG ink costs a DOM
       * node per stroke rather than a page-sized bitmap, there is no reason to
       * hold markup for pages nobody is looking at; the strokes live in the
       * reader's markup, so the surface is a view that can be rebuilt at will.
       *
       * The touchmove guard rides on the container above, which is always
       * mounted — the Pencil must be held off the scroller whether or not this
       * particular page currently has an ink layer. */}
      {visible && (
        <div className="absolute inset-0">
          <InkSurface
            space={space}
            strokes={inked}
            // The reader above holds every committed stroke, so it is never
            // behind this surface and its Undo has to be able to reach in.
            adoptWhileDirty
            tool={tool === 'read' ? null : tool}
            size={size}
            color={color}
            palette={NEWSPAPER_PALETTE}
            // Drawn over a scrolling column of pages: a finger has to keep
            // scrolling and pinch-zooming in every tool.
            touchPolicy="scroll"
            guardRef={container}
            minPointDistance={MIN_POINT_DISTANCE}
            maxPointsPerStroke={MAX_POINTS_PER_STROKE}
            // No backdrop: the PDF page underneath has to show through, which
            // is the whole reason this surface never paints over anything.
            onEdit={next =>
              onEdit(
                number,
                next.map(s => toWireStroke(s, ratio))
              )
            }
            label={`Page ${number} markup`}
          />
        </div>
      )}
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
  // Bumped when an edit is refused, to pull the ink layers back into line with
  // the markup — they have already drawn the stroke by the time we say no.
  const [reseed, setReseed] = useState(0);
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

  // Opening the issue is half of what dates its Journal card — the other half
  // is saving markup — so it is recorded on its own rather than inferred from
  // the markup fetch below. Fire-and-forget: a reader that cannot reach the
  // server still has a paper to read, and the stamp is not worth an error
  // banner over.
  useEffect(() => {
    void api.newspapers.markOpened(issue.date).catch(() => {});
  }, [issue.date]);

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
      // The mark is already on the surface that drew it; take it back off,
      // rather than leaving one on screen that will never be saved.
      setReseed(n => n + 1);
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
                reseed={reseed}
                onEdit={(page, next) =>
                  change(setStrokesOn(markupRef.current, page, next))
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
