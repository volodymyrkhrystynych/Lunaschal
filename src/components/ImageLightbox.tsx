import { useState } from 'react';

/** Local zoom state for a thumbnail filmstrip: which image (if any) is
 * currently shown full-screen. */
export function useLightbox() {
  const [src, setSrc] = useState<string | null>(null);
  return { src, open: setSrc, close: () => setSrc(null) };
}

/** Full-screen click-to-close preview of one image, opened by a thumbnail
 * button elsewhere. Used by the journal entry/attachment filmstrips, the
 * food log, the Ideas sketch strip, and the newspaper front-page preview. */
export function ImageLightbox({
  src,
  onClose,
  whiteBg = false,
  alt = '',
}: {
  src: string | null;
  onClose: () => void;
  whiteBg?: boolean;
  alt?: string;
}) {
  if (!src) return null;
  return (
    <div
      className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <img
        src={src}
        alt={alt}
        className={`max-w-full max-h-full rounded-lg shadow-2xl${whiteBg ? ' bg-white' : ''}`}
        onClick={e => e.stopPropagation()}
      />
      <button
        onClick={onClose}
        className="absolute top-4 right-4 text-white/80 hover:text-white text-2xl"
      >
        ✕
      </button>
    </div>
  );
}
