import { useEffect, useMemo, useState } from 'react';

const LOCAL_IMAGE = /data-src=(['"])(\/api\/email\/images\/[a-f0-9]+)\1/gi;

function iframeDocument(
  html: string,
  imagesLoaded: boolean,
  fitWidth: boolean
) {
  const body = imagesLoaded
    ? html.replace(
        LOCAL_IMAGE,
        (_match, quote, src) => `src=${quote}${src}${quote}`
      )
    : html;
  const fitRules = fitWidth
    ? `table { max-width: 100% !important; }
       img { max-width: 100% !important; height: auto !important; }`
    : '';

  return `<!doctype html><html><head><meta charset="utf-8">
    <base target="_blank">
    <style>
      :root { color-scheme: light; }
      html, body { margin: 0; padding: 0; background: #fff; color: #161616; }
      body { font: 14px/1.5 Arial, Helvetica, sans-serif; overflow-wrap: anywhere; }
      a { color: #0a66c2; }
      img[data-src]:not([src]) { display: inline-block; min-width: 1rem;
        min-height: 1rem; border: 1px dashed #888; opacity: .35; }
      ${fitRules}
    </style></head><body>${body}</body></html>`;
}

/** Render sanitized email markup in its own CSS and layout boundary. */
export function EmailBody({ html, text }: { html: string; text: string }) {
  const [imagesLoaded, setImagesLoaded] = useState(false);
  const [fitWidth, setFitWidth] = useState(true);

  useEffect(() => {
    setImagesLoaded(false);
    setFitWidth(true);
  }, [html]);

  const imageCount = useMemo(
    () => (html ? (html.match(/<img\b/gi) || []).length : 0),
    [html]
  );
  const srcDoc = useMemo(
    () => iframeDocument(html, imagesLoaded, fitWidth),
    [html, imagesLoaded, fitWidth]
  );

  if (!html) {
    return (
      <pre className="whitespace-pre-wrap break-words font-sans text-sm text-[var(--color-text)]">
        {text || '(no body)'}
      </pre>
    );
  }

  return (
    <div className="flex min-h-[32rem] h-full flex-col gap-2">
      <div className="flex items-center gap-2">
        {imageCount > 0 && !imagesLoaded && (
          <>
            <span className="text-xs text-[var(--color-text-muted)] flex-1">
              {imageCount} image{imageCount === 1 ? '' : 's'} not shown
            </span>
            <button
              onClick={() => setImagesLoaded(true)}
              className="text-xs px-2 py-1 rounded border border-white/10 text-[var(--color-text)] hover:border-[var(--color-primary)]"
            >
              Load images
            </button>
          </>
        )}
        {(imageCount === 0 || imagesLoaded) && <span className="flex-1" />}
        <button
          onClick={() => setFitWidth(value => !value)}
          className="text-xs px-2 py-1 rounded border border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          {fitWidth ? 'Original width' : 'Fit width'}
        </button>
      </div>
      <iframe
        title="Email content"
        sandbox="allow-popups allow-popups-to-escape-sandbox"
        srcDoc={srcDoc}
        className="w-full flex-1 min-h-[30rem] rounded bg-white border-0"
      />
    </div>
  );
}
