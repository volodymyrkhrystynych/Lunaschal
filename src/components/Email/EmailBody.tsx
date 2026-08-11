import { useEffect, useMemo, useRef, useState } from 'react';

/**
 * Renders an email body.
 *
 * The HTML arrives already sanitized by backend/email/sanitize.py — the same
 * import-time contract fanfic chapters use, so this component never sanitizes
 * and never needs to.
 *
 * Images are the interesting part. The sanitizer has already rewritten every
 * `src` to `data-src="/api/email/images/<hash>"`, so nothing loads on open and
 * the sender learns nothing. Clicking "Load images" promotes those to real
 * `src` attributes, which by then point at our own origin — the bytes were
 * fetched server-side once and stored content-addressed, so a logo repeated
 * across a thousand messages is one file and one request.
 */
export function EmailBody({ html, text }: { html: string; text: string }) {
  const [imagesLoaded, setImagesLoaded] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // A different email must start with images off again — consenting to load
  // one sender's pictures is not consent for the next one's.
  useEffect(() => {
    setImagesLoaded(false);
  }, [html]);

  const imageCount = useMemo(() => {
    if (!html) return 0;
    return (html.match(/<img\b/gi) || []).length;
  }, [html]);

  useEffect(() => {
    const root = containerRef.current;
    if (!root || !imagesLoaded) return;
    root.querySelectorAll<HTMLImageElement>('img[data-src]').forEach(img => {
      const src = img.getAttribute('data-src');
      // Only ever promote a same-origin path. The sanitizer guarantees this
      // shape, and re-checking here means a stored row that predates that
      // guarantee still can't turn into a remote fetch.
      if (src && src.startsWith('/api/email/images/')) {
        img.src = src;
      }
    });
  }, [imagesLoaded, html]);

  if (!html) {
    return (
      <pre className="whitespace-pre-wrap break-words font-sans text-sm text-[var(--color-text)]">
        {text || '(no body)'}
      </pre>
    );
  }

  return (
    <div>
      {imageCount > 0 && !imagesLoaded && (
        <div className="flex items-center gap-2 mb-3 p-2 rounded border border-white/10 bg-white/5">
          <span className="text-xs text-[var(--color-text-muted)] flex-1">
            {imageCount} image{imageCount === 1 ? '' : 's'} not shown
          </span>
          <button
            onClick={() => setImagesLoaded(true)}
            className="text-xs px-2 py-1 rounded border border-white/10 text-[var(--color-text)] hover:border-[var(--color-primary)] hover:text-[var(--color-primary)] transition-colors"
          >
            Load images
          </button>
        </div>
      )}
      <div
        ref={containerRef}
        className="email-body text-sm text-[var(--color-text)]"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  );
}
