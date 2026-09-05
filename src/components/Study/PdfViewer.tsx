import { useEffect, useRef, useState } from 'react';
import * as pdfjs from 'pdfjs-dist';
import type { PDFDocumentProxy } from 'pdfjs-dist';

// pdf.js parses in a worker. Vite resolves this `new URL(..., import.meta.url)`
// at build time and bundles the worker as its own chunk, so nothing is fetched
// from a CDN — which also means the desktop shell works with no network.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url
).toString();

// The app renders PDFs itself rather than handing the file to an <embed>: the
// browser's built-in viewer shows page one and nothing else inside an iframe on
// iPad Safari, which is half the devices this tab is for. Rendering also gives
// us a real page number to hang "resume where I left off" on later.

const ZOOM_STEPS = [0.6, 0.75, 0.9, 1, 1.25, 1.5, 2, 3];
const DEFAULT_ZOOM_INDEX = 3;

interface Props {
  fileUrl: string;
  /** The page to open on, from wherever this reader last left off. */
  initialPage?: number;
  /** Fires when the page under the top of the viewport changes. */
  onPageChange?: (page: number) => void;
}

export function PdfViewer({ fileUrl, initialPage, onPageChange }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pagesRef = useRef<HTMLDivElement>(null);
  const docRef = useRef<PDFDocumentProxy | null>(null);
  const [pageCount, setPageCount] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [zoomIndex, setZoomIndex] = useState(DEFAULT_ZOOM_INDEX);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // The page we still owe the reader. Consumed *inside* the render loop, as
  // the matching canvas is appended: the loop builds pages sequentially in an
  // async IIFE, so `canvas[data-page="214"]` does not exist yet at load time
  // and scrolling to it on mount would silently do nothing. Same problem
  // Fanfic's Reader solves for chapter text.
  const pendingPageRef = useRef<number | null>(
    initialPage && initialPage > 1 ? Math.floor(initialPage) : null
  );
  // Read by the render effect, which must not re-run when the page changes.
  const currentPageRef = useRef(1);
  currentPageRef.current = currentPage;

  // Load the document once per file.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const task = pdfjs.getDocument({ url: fileUrl });
    task.promise.then(
      doc => {
        if (cancelled) {
          void doc.destroy();
          return;
        }
        docRef.current = doc;
        setPageCount(doc.numPages);
        setLoading(false);
      },
      (e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : 'Could not open this PDF.');
        setLoading(false);
      }
    );
    return () => {
      cancelled = true;
      void task.destroy();
      docRef.current = null;
    };
  }, [fileUrl]);

  // Render every page into its own canvas. Re-runs on zoom because a canvas is
  // rasterized at one scale: scaling it with CSS is how a PDF goes blurry.
  useEffect(() => {
    const doc = docRef.current;
    const host = pagesRef.current;
    if (!doc || !host || pageCount === 0) return;

    let cancelled = false;
    // Re-arm the pending page across a zoom change. The loop tears every
    // canvas down and rebuilds it, which sends the scroller back to the top —
    // and the scroll handler would then report page 1 and overwrite the
    // position with it. Zooming is not moving.
    if (pendingPageRef.current === null && currentPageRef.current > 1) {
      pendingPageRef.current = currentPageRef.current;
    }
    host.replaceChildren();
    const scale = ZOOM_STEPS[zoomIndex];
    // Render at the device's real pixel density and scale back down in CSS,
    // or text is soft on a HiDPI screen.
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    (async () => {
      for (let number = 1; number <= doc.numPages; number += 1) {
        if (cancelled) return;
        const page = await doc.getPage(number);
        if (cancelled) return;
        const viewport = page.getViewport({ scale: scale * dpr });

        const canvas = document.createElement('canvas');
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        canvas.style.width = `${viewport.width / dpr}px`;
        canvas.style.height = `${viewport.height / dpr}px`;
        canvas.className =
          'block mx-auto mb-3 shadow-lg max-w-full bg-white rounded-sm';
        canvas.dataset.page = String(number);

        const context = canvas.getContext('2d');
        if (!context) continue;
        host.appendChild(canvas);
        if (pendingPageRef.current === number) {
          pendingPageRef.current = null;
          scrollTo(canvas);
          setCurrentPage(number);
        }
        await page.render({ canvasContext: context, viewport }).promise;
      }
    })().catch((e: unknown) => {
      if (!cancelled) {
        setError(e instanceof Error ? e.message : 'Could not render this PDF.');
      }
    });

    return () => {
      cancelled = true;
    };
  }, [pageCount, zoomIndex]);

  // Which page is under the top of the viewport — the number in the toolbar.
  const onScroll = () => {
    const scroller = scrollRef.current;
    const host = pagesRef.current;
    if (!scroller || !host) return;
    const top = scroller.scrollTop;
    let visible = 1;
    for (const child of Array.from(host.children)) {
      const canvas = child as HTMLCanvasElement;
      if (canvas.offsetTop - host.offsetTop <= top + 8) {
        visible = Number(canvas.dataset.page ?? visible);
      } else break;
    }
    // While a restore is still outstanding the pages under the viewport are
    // whatever has rendered so far, not where the reader is. Reporting that
    // would overwrite the very position we are on our way to.
    if (pendingPageRef.current !== null || visible === currentPage) return;
    setCurrentPage(visible);
    onPageChange?.(visible);
  };

  const scrollTo = (canvas: HTMLCanvasElement) => {
    const host = pagesRef.current;
    const scroller = scrollRef.current;
    if (!host || !scroller) return;
    scroller.scrollTo({ top: canvas.offsetTop - host.offsetTop });
  };

  const goToPage = (number: number) => {
    const target = pagesRef.current?.querySelector<HTMLCanvasElement>(
      `canvas[data-page="${number}"]`
    );
    if (target) scrollTo(target);
  };

  if (error) {
    return (
      <div className="flex-1 flex items-center justify-center p-6 text-center text-sm text-[var(--color-text-muted)]">
        {error}
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="shrink-0 flex items-center gap-2 px-2 py-1 border-b border-white/10 bg-[var(--color-surface)] text-xs text-[var(--color-text-muted)]">
        <button
          type="button"
          onClick={() => goToPage(Math.max(1, currentPage - 1))}
          className="px-2 py-0.5 rounded hover:bg-white/10"
          aria-label="Previous page"
        >
          ‹
        </button>
        {/* Fixed width so the toolbar doesn't reflow as the number grows —
            the same reason Paper's save indicator has a fixed slot. */}
        <span className="w-16 text-center tabular-nums">
          {loading ? '…' : `${currentPage} / ${pageCount}`}
        </span>
        <button
          type="button"
          onClick={() => goToPage(Math.min(pageCount, currentPage + 1))}
          className="px-2 py-0.5 rounded hover:bg-white/10"
          aria-label="Next page"
        >
          ›
        </button>
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => setZoomIndex(i => Math.max(0, i - 1))}
          className="px-2 py-0.5 rounded hover:bg-white/10"
          aria-label="Zoom out"
        >
          −
        </button>
        <span className="w-12 text-center tabular-nums">
          {Math.round(ZOOM_STEPS[zoomIndex] * 100)}%
        </span>
        <button
          type="button"
          onClick={() =>
            setZoomIndex(i => Math.min(ZOOM_STEPS.length - 1, i + 1))
          }
          className="px-2 py-0.5 rounded hover:bg-white/10"
          aria-label="Zoom in"
        >
          +
        </button>
      </div>
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="flex-1 overflow-auto bg-[var(--color-bg)] p-3"
      >
        {loading && (
          <div className="text-center text-sm text-[var(--color-text-muted)]">
            Loading…
          </div>
        )}
        <div ref={pagesRef} />
      </div>
    </div>
  );
}
