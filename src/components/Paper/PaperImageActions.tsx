import { ROTATION_STEP, type PageImage } from '@/lib/paperImages';

interface PaperImageActionsProps {
  image: PageImage;
  onRotate: (delta: number) => void;
  onFlip: () => void;
  onToggleLock: () => void;
  onDelete: () => void;
}

/**
 * Transform controls for the selected picture.
 *
 * Rendered at a fixed spot rather than following the image: it is one bar of a
 * constant size, so it can't reflow under the stylus the way the old tool row
 * did. Rotation is quarter turns only, which is what actually gets a photo
 * upright — there is no free-rotate handle on purpose.
 */
export function PaperImageActions({
  image,
  onRotate,
  onFlip,
  onToggleLock,
  onDelete,
}: PaperImageActionsProps) {
  const btn =
    'min-w-[44px] min-h-[44px] px-2 rounded-md text-sm font-medium bg-[var(--color-surface)] hover:bg-white/10 text-[var(--color-text)]';

  return (
    <div
      data-testid="paper-image-actions"
      className="absolute left-1/2 -translate-x-1/2 bottom-3 flex items-center gap-1 p-1 rounded-lg bg-[var(--color-bg)]/95 border border-white/10 shadow-lg"
    >
      {image.locked ? (
        // A locked image offers exactly one action: unlock. Showing the
        // transforms greyed out would just invite taps that do nothing.
        <button
          type="button"
          onClick={onToggleLock}
          className={btn}
          aria-label="Unlock image"
          title="Unlock image"
        >
          🔒
        </button>
      ) : (
        <>
          <button
            type="button"
            onClick={() => onRotate(-ROTATION_STEP)}
            className={btn}
            aria-label="Rotate left 90°"
            title="Rotate left 90°"
          >
            ↺
          </button>
          <button
            type="button"
            onClick={() => onRotate(ROTATION_STEP)}
            className={btn}
            aria-label="Rotate right 90°"
            title="Rotate right 90°"
          >
            ↻
          </button>
          <button
            type="button"
            onClick={onFlip}
            className={btn}
            aria-label="Flip horizontally"
            title="Flip horizontally"
          >
            ⇋
          </button>
          <button
            type="button"
            onClick={onToggleLock}
            className={btn}
            aria-label="Lock image"
            title="Lock in place"
          >
            🔓
          </button>
          <button
            type="button"
            onClick={onDelete}
            className={`${btn} text-red-400`}
            aria-label="Delete image"
            title="Delete image"
          >
            ✕
          </button>
        </>
      )}
    </div>
  );
}
