import { useRef } from 'react';
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
 * One component, used by both the new-entry composer and the editor on an
 * existing entry, so the two cannot drift — which is how the pair above ended up
 * identical in the first place.
 */
export function AttachmentButtons({
  onFiles,
  disabled = false,
  idPrefix,
  extra,
}: {
  onFiles: (files: File[]) => void;
  disabled?: boolean;
  /** Distinguishes the composer's inputs from an entry's in the DOM. */
  idPrefix: string;
  /** Rendered after the buttons — the editor's Paste button and status line. */
  extra?: React.ReactNode;
}) {
  const cameraRef = useRef<HTMLInputElement>(null);
  const photoRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const touch = isTouchDevice();

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
      {extra}
    </div>
  );
}
