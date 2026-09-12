// Painting committed ink onto a 2D canvas context.
//
// One function, because there are two callers and they must not drift: the
// Paper editor's page snapshot (src/components/Paper/PaperSurface.tsx) and the
// newspaper reader's thumbnail worker (src/components/NewspaperReader.tsx).
// The geometry itself is src/lib/inkPath.ts's — this is only the three
// decisions that turn a path into paint: which colour a stroke resolves to,
// what a highlighter does to the alpha, and what to do where Path2D does not
// exist. See src/components/ink/CLAUDE.md: a fix to how ink behaves is written
// once.
//
// Not in src/lib/inkPath.ts, which is pure geometry with no DOM in it.

import { strokeColor, type InkPalette, type Stroke } from '@/lib/ink';
import { strokePathData } from '@/lib/inkPath';

/** Fill every stroke into `ctx`, in the context's current transform.
 *
 * The caller owns the transform: strokes are in their surface's own units
 * (tenths of a millimetre for Paper, thousandths of a page width for the
 * newspaper), so scaling them onto the canvas is the one thing this cannot
 * know.
 */
export function paintStrokes(
  ctx: CanvasRenderingContext2D,
  strokes: Stroke[],
  palette: InkPalette
): void {
  // Path2D is in every browser this runs in; jsdom has none, and a snapshot
  // without ink is a better test artefact than a crash.
  if (typeof Path2D === 'undefined') return;
  for (const stroke of strokes) {
    const d = strokePathData(stroke);
    if (!d) continue;
    ctx.fillStyle = strokeColor(stroke, palette);
    ctx.globalAlpha =
      stroke.tool === 'highlighter' ? palette.highlightAlpha : 1;
    ctx.fill(new Path2D(d));
  }
  // Restored because a shared helper cannot assume what follows it. Paper's
  // loop got away without this only because toBlob came next.
  ctx.globalAlpha = 1;
}
