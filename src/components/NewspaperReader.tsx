import { useEffect, useRef, useState } from 'react';
import * as pdfjs from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import {
  api,
  ApiError,
  type NewspaperIssue,
  type NewspaperMarkup,
  type NewspaperStroke,
} from '../hooks/api';

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
type Tool = 'read' | NewspaperStroke['tool'];

function Page({
  pdf,
  number,
  tool,
  strokes,
  onStroke,
}: {
  pdf: pdfjs.PDFDocumentProxy;
  number: number;
  tool: Tool;
  strokes: NewspaperStroke[];
  onStroke: (stroke: NewspaperStroke) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const drawing = useRef<NewspaperStroke | null>(null);
  const finger = useRef<{ id: number; y: number } | null>(null);
  const [preview, setPreview] = useState<NewspaperStroke | null>(null);
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

  function point(event: React.PointerEvent<SVGSVGElement>): [number, number] {
    const rect = event.currentTarget.getBoundingClientRect();
    return [
      Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    ];
  }
  const all = preview ? [...strokes, preview] : strokes;
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
        className="absolute inset-0 w-full h-full"
        viewBox={`0 0 1000 ${1000 * ratio}`}
        style={{ touchAction: tool === 'read' ? 'pan-y pinch-zoom' : 'none' }}
        onPointerDown={event => {
          // Fingers always scroll. Pencil (or a mouse for testing) writes.
          if (tool === 'read' || !event.isPrimary) return;
          if (event.pointerType === 'touch') {
            if (!drawing.current) {
              finger.current = { id: event.pointerId, y: event.clientY };
              event.currentTarget.setPointerCapture(event.pointerId);
            }
            return;
          }
          event.preventDefault();
          event.currentTarget.setPointerCapture(event.pointerId);
          drawing.current = { page: number, tool, points: [point(event)] };
          setPreview({ ...drawing.current });
        }}
        onPointerMove={event => {
          if (finger.current?.id === event.pointerId) {
            container.current?.parentElement?.scrollBy(
              0,
              finger.current.y - event.clientY
            );
            finger.current.y = event.clientY;
            return;
          }
          if (
            !drawing.current ||
            !event.currentTarget.hasPointerCapture(event.pointerId)
          )
            return;
          if (drawing.current.points.length < 10000)
            drawing.current.points.push(point(event));
          setPreview({ ...drawing.current });
        }}
        onPointerUp={event => {
          if (finger.current?.id === event.pointerId) {
            finger.current = null;
            return;
          }
          if (
            !drawing.current ||
            !event.currentTarget.hasPointerCapture(event.pointerId)
          )
            return;
          const stroke = drawing.current;
          drawing.current = null;
          setPreview(null);
          onStroke(stroke);
          event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerCancel={() => {
          finger.current = null;
          drawing.current = null;
          setPreview(null);
        }}
      >
        {all.map((stroke, i) => (
          <polyline
            key={i}
            points={stroke.points
              .map(([x, y]) => `${x * 1000},${y * 1000 * ratio}`)
              .join(' ')}
            fill="none"
            stroke={stroke.tool === 'pen' ? '#1756ad' : '#ffdb00'}
            strokeWidth={stroke.tool === 'pen' ? 2 : 16}
            opacity={stroke.tool === 'pen' ? 1 : 0.35}
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

export function NewspaperReader({
  issue,
  onClose,
}: {
  issue: NewspaperIssue;
  onClose: () => void;
}) {
  const [pdf, setPdf] = useState<pdfjs.PDFDocumentProxy | null>(null);
  const [strokes, setStrokes] = useState<NewspaperStroke[]>([]);
  const [tool, setTool] = useState<Tool>('read');
  const [status, setStatus] = useState('Loading…');
  const [ready, setReady] = useState(false);
  const [exporting, setExporting] = useState(false);
  const state = useRef<NewspaperMarkup>({ revision: 0, strokes: [] });
  const saving = useRef(false);
  const dirty = useRef(false);
  const conflict = useRef(false);
  const key = `newspaper-markup:${issue.date}`;

  useEffect(() => {
    const loading = pdfjs.getDocument({ url: issue.pdfUrl });
    let active = true;
    void Promise.all([loading.promise, api.newspapers.markup(issue.date)])
      .then(([document, markup]) => {
        if (!active) return;
        let draft: NewspaperMarkup | null = null;
        try {
          draft = JSON.parse(localStorage.getItem(key) || 'null');
        } catch {
          /* No readable draft. */
        }
        if (draft && Array.isArray(draft.strokes)) {
          conflict.current = draft.revision !== markup.revision;
          state.current = draft;
          dirty.current = true;
          setStatus(
            conflict.current
              ? 'A local draft conflicts with the server. Export it before reopening on another device.'
              : 'Recovered local markup; saving…'
          );
        } else {
          state.current = markup;
          setStatus('Saved');
        }
        setStrokes(state.current.strokes);
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
    const snapshot = state.current;
    setStatus('Saving…');
    try {
      const result = await api.newspapers.saveMarkup(issue.date, snapshot);
      const changed = state.current.strokes !== snapshot.strokes;
      state.current = {
        revision: result.revision,
        strokes: state.current.strokes,
      };
      dirty.current = changed;
      if (changed) localStorage.setItem(key, JSON.stringify(state.current));
      else localStorage.removeItem(key);
      setStatus(changed ? 'Saving…' : 'Saved');
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) conflict.current = true;
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
  }, [ready]);

  function change(next: NewspaperStroke[]) {
    if (
      next.length > 10000 ||
      next.reduce((count, stroke) => count + stroke.points.length, 0) > 100000
    ) {
      setStatus(
        'This issue has reached its markup limit. Export it before adding more marks.'
      );
      return;
    }
    state.current = { ...state.current, strokes: next };
    dirty.current = true;
    setStrokes(next);
    try {
      localStorage.setItem(key, JSON.stringify(state.current));
      setStatus('Saved on this iPad; syncing…');
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
      for (const stroke of state.current.strokes) {
        const source = await pdf.getPage(stroke.page);
        const view = source.getViewport({ scale: 1 });
        const target = output.getPage(stroke.page - 1);
        const points = stroke.points.map(([x, y]) =>
          view.convertToPdfPoint(x * view.width, y * view.height)
        );
        for (let i = 0; i < points.length; i++) {
          const start = points[Math.max(0, i - 1)],
            end = points[i];
          target.drawLine({
            start: { x: start[0], y: start[1] },
            end: { x: end[0], y: end[1] },
            thickness: view.width * (stroke.tool === 'pen' ? 0.002 : 0.016),
            color:
              stroke.tool === 'pen' ? rgb(0.09, 0.34, 0.68) : rgb(1, 0.86, 0),
            opacity: stroke.tool === 'pen' ? 1 : 0.35,
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
        {(['read', 'pen', 'highlight'] as const).map(value => (
          <button
            key={value}
            aria-pressed={tool === value}
            disabled={!ready}
            className={`p-2 rounded ${tool === value ? 'bg-blue-700 text-white' : ''}`}
            onClick={() => setTool(value)}
          >
            {value === 'read' ? 'Read' : value === 'pen' ? 'Pen' : 'Highlight'}
          </button>
        ))}
        <button
          className="p-2"
          disabled={!strokes.length}
          onClick={() => change(strokes.slice(0, -1))}
        >
          Undo
        </button>
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
                state.current = latest;
                dirty.current = false;
                conflict.current = false;
                setStrokes(latest.strokes);
                setStatus('Saved');
              } catch (e) {
                setStatus((e as Error).message);
              }
            }}
          >
            Use server copy
          </button>
        )}
        {ready && status !== 'Saved' && (
          <button
            className="p-2"
            onClick={() => {
              try {
                localStorage.setItem(key, JSON.stringify(state.current));
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
        <span role="status" className="text-xs">
          {status}
        </span>
        {!ready && (
          <button className="p-2" onClick={onClose}>
            Close
          </button>
        )}
      </div>
      <p className="text-xs px-3 py-1">
        Scroll with your finger. Write or highlight with Apple Pencil. Pages fit
        the width.
      </p>
      <div className="flex-1 overflow-y-auto overscroll-contain">
        {pdf &&
          Array.from({ length: pdf.numPages }, (_, i) => (
            <Page
              key={i}
              pdf={pdf}
              number={i + 1}
              tool={tool}
              strokes={strokes.filter(s => s.page === i + 1)}
              onStroke={stroke => change([...state.current.strokes, stroke])}
            />
          ))}
      </div>
    </div>
  );
}
