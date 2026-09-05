import { imageCenter, type PageImage } from '@/lib/paperImages';

/** Diameter of the badge, in CSS pixels. Not a touch target — nothing in this
 * layer is pressable — but it has to read as a warning at arm's length. */
const BADGE_PX = 32;

interface PaperImageWarningsProps {
  images: PageImage[];
  /** Image id → why its upload was refused. Only ids in here are marked, so a
   * picture merely waiting for the next Save carries no warning: that is the
   * ordinary state of every picture between pasting it and saving. */
  failures: Record<string, string>;
  /** CSS pixels per page unit — the same factor the canvas renders with. */
  scale: number;
}

/**
 * A ⚠ over every picture on this page whose upload the server refused.
 *
 * The banner above the page says *that* something failed; this says *which*.
 * Nothing else on the page can: a refused picture is drawn from the blob it was
 * pasted from, exactly like one that saved cleanly, so a paper with four
 * pictures gave no clue which of them was the one still stuck on the device.
 *
 * Mounted in every mode, unlike `PaperImageLayer`, and `pointer-events-none`
 * throughout — the one thing an overlay above the ink canvas must never do is
 * swallow a stroke. Acting on it stays with the Retry in the banner, which is a
 * button in the chrome rather than one more thing to hit over the page.
 */
export function PaperImageWarnings({
  images,
  failures,
  scale,
}: PaperImageWarningsProps) {
  const marked = images.filter(img => failures[img.id]);
  if (marked.length === 0) return null;
  return (
    <div
      data-testid="paper-image-warnings"
      className="absolute inset-0 pointer-events-none"
    >
      {marked.map(img => {
        // Rotation-invariant, so the badge sits on the picture at any angle and
        // is never mirrored by a flip — which is also why it is a sibling of
        // the outline rather than a child of it.
        const center = imageCenter(img);
        return (
          <div key={img.id}>
            <div
              className="absolute border-2 border-dashed border-amber-500"
              style={{
                left: img.x * scale,
                top: img.y * scale,
                width: img.width * scale,
                height: img.height * scale,
                transform: `rotate(${img.rotation}deg) scaleX(${
                  img.flipped ? -1 : 1
                })`,
                transformOrigin: 'center',
              }}
            />
            <div
              data-testid={`paper-image-warning-${img.id}`}
              role="img"
              aria-label={`This picture never reached the server: ${failures[img.id]}`}
              title={`${failures[img.id]} — still held on this device`}
              className="absolute flex items-center justify-center rounded-full bg-amber-500 text-black shadow ring-2 ring-white"
              style={{
                width: BADGE_PX,
                height: BADGE_PX,
                left: center.x * scale - BADGE_PX / 2,
                top: center.y * scale - BADGE_PX / 2,
                fontSize: BADGE_PX * 0.55,
                lineHeight: 1,
              }}
            >
              ⚠
            </div>
          </div>
        );
      })}
    </div>
  );
}
