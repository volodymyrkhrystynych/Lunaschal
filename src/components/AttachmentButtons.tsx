import { useRef, useState } from 'react';
import { ACCEPT_IMAGE } from '../lib/journalAttachments';
import { isTouchDevice } from '../lib/deviceInput';

/**
 * The buttons that put a file on a journal entry.
 *
 * There used to be two — "Add audio or video" and "Add photo" — and they were
 * the same button: the same `<input type="file">`, the same handler, the same
 * destination, differing only in an `accept` string. Neither opened a camera and
 * neither took more than one file at a time, while paste and drop happily took
 * several. These three are the three things a person actually wants to do:
 *
 * - **Take a photo** — `capture="environment"`, which opens the camera directly
 *   on a phone. Rendered only on a touch device, because the attribute is
 *   ignored on a desktop and the button would be a second file dialog wearing a
 *   camera icon. `isTouchDevice` is read once per render, not cached at module
 *   scope, so it survives the window being resized into a phone-shaped one and
 *   is trivially stubbable in a test.
 * - **Photo** — the camera roll or the file dialog, images only.
 * - **File** — no `accept` at all. Anything. The backend stores an upload it
 *   doesn't recognise as `kind='file'` rather than refusing it.
 *
 * All three take `multiple`, which none of the old ones did.
 *
 * A fourth button, **Video**, appears only where `onLink` is supplied: it takes
 * a YouTube URL rather than a file, and the video is downloaded server-side.
 * Deliberately a button and a field rather than sniffing the composer's text
 * for a URL — a link pasted into a sentence is usually part of the sentence.
 *
 * One component, used by both the new-entry composer and the editor on an
 * existing entry, so the two cannot drift — which is how the pair above ended up
 * identical in the first place.
 */
export function AttachmentButtons({
  onFiles,
  onLink,
  disabled = false,
  idPrefix,
  extra,
}: {
  onFiles: (files: File[]) => void;
  /** Supplied where a YouTube link can be attached; omit to hide the button. */
  onLink?: (url: string) => void;
  disabled?: boolean;
  /** Distinguishes the composer's inputs from an entry's in the DOM. */
  idPrefix: string;
  /** Rendered after the buttons — the editor's Paste button and status line. */
  extra?: React.ReactNode;
}) {
  const cameraRef = useRef<HTMLInputElement>(null);
  const photoRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [linkOpen, setLinkOpen] = useState(false);
  const [link, setLink] = useState('');
  const touch = isTouchDevice();

  const commitLink = () => {
    const url = link.trim();
    if (!url) {
      setLinkOpen(false);
      return;
    }
    onLink?.(url);
    setLink('');
    setLinkOpen(false);
  };

  const pick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    // Reset first: picking the same file twice in a row otherwise fires no
    // change event, which reads as "the button stopped working".
    e.target.value = '';
    if (files.length) onFiles(files);
  };

  const cls =
    'px-2 py-1 text-xs rounded border border-white/10 text-[var(--color-text-muted)]' +
    ' hover:text-[var(--color-text)] hover:border-white/20 disabled:opacity-50';

  return (
    <div className="flex items-center gap-2 flex-wrap pt-1">
      {touch && (
        <input
          ref={cameraRef}
          type="file"
          accept="image/*"
          capture="environment"
          onChange={pick}
          className="hidden"
          data-testid={`${idPrefix}-camera-input`}
        />
      )}
      <input
        ref={photoRef}
        type="file"
        accept={ACCEPT_IMAGE}
        multiple
        onChange={pick}
        className="hidden"
        data-testid={`${idPrefix}-image-input`}
      />
      <input
        ref={fileRef}
        type="file"
        multiple
        onChange={pick}
        className="hidden"
        data-testid={`${idPrefix}-file-input`}
      />
      {touch && (
        <button
          type="button"
          onClick={() => cameraRef.current?.click()}
          disabled={disabled}
          className={cls}
        >
          📷 Take a photo
        </button>
      )}
      <button
        type="button"
        onClick={() => photoRef.current?.click()}
        disabled={disabled}
        className={cls}
      >
        🖼 Photo
      </button>
      <button
        type="button"
        onClick={() => fileRef.current?.click()}
        disabled={disabled}
        className={cls}
      >
        📎 File
      </button>
      {onLink && (
        <button
          type="button"
          onClick={() => setLinkOpen(o => !o)}
          disabled={disabled}
          className={cls}
          data-testid={`${idPrefix}-link-button`}
        >
          🔗 Video
        </button>
      )}
      {extra}
      {onLink && linkOpen && (
        <div className="flex items-center gap-2 w-full">
          <input
            autoFocus
            value={link}
            onChange={e => setLink(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') {
                e.preventDefault();
                commitLink();
              }
              if (e.key === 'Escape') {
                setLink('');
                setLinkOpen(false);
              }
            }}
            placeholder="Paste a YouTube link"
            className="flex-1 min-w-0 px-2 py-1 text-xs rounded border border-white/10 bg-transparent text-[var(--color-text)]"
            data-testid={`${idPrefix}-link-input`}
          />
          <button
            type="button"
            onClick={commitLink}
            disabled={disabled || !link.trim()}
            className={cls}
            data-testid={`${idPrefix}-link-add`}
          >
            Add
          </button>
        </div>
      )}
    </div>
  );
}
