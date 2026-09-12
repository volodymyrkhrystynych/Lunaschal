# The ink layer (`src/components/ink/`, `src/lib/ink.ts`, `src/lib/inkPath.ts`, `src/lib/inkRaster.ts`, `src/lib/inkPanel.ts`)

Every drawing surface in the app is built from this. There are three of them and
they share one implementation, so a fix to how ink behaves is written once:

| Surface              | Where                                     | Notes                                                         |
| -------------------- | ----------------------------------------- | ------------------------------------------------------------- |
| Paper                | `src/components/Paper/`                   | An A4 page that gets saved, with pictures under the ink       |
| The Study desk       | `src/components/Study/StudyPaperPane.tsx` | Mounts `PaperEditor embedded`; **no drawing code of its own** |
| The newspaper reader | `src/components/NewspaperReader.tsx`      | Markup over a scrolling column of PDF pages                   |

Before this, Paper and the newspaper were two independent implementations —
canvas versus SVG, page units versus normalised coordinates, pressure versus
none — and the newspaper had no eraser, no redo and no width choice because
those had only ever been written on the other side. **If a fourth surface
appears, it supplies a coordinate space, a palette and a touch policy. It does
not supply a stroke model or a pointer loop.**

- **`src/lib/ink.ts` knows nothing about how big anything is.** Strokes,
  snapshot undo/redo, the geometric eraser, simplification, pressure, the
  tap/swipe predicates. Every coordinate is in whatever space the surface above
  declares, and the transform to the screen is the SVG's own viewBox. Page-
  specific geometry lives beside the surface that owns it: `src/lib/paper.ts`
  for the A4 sheet, `src/lib/newspaperMarkup.ts` for a PDF page.

- **Ink is SVG, and that is a decision about magnification.** A stroke is
  geometry in the page's own units, so it stays sharp at whatever zoom the page
  is read at — a newspaper is pinch-zoomed to read small print, and a bitmap
  would soften exactly when it is being looked at hardest. It also means a
  stroke costs one DOM node instead of a page-sized bitmap, which is what makes
  a several-hundred-page issue affordable.

- **A `<polyline>` carries one width, so pressure needs an outline**
  (`src/lib/inkPath.ts`). A tapering stroke is drawn as the outline of a
  variable-width ribbon and filled: one `<path>` per stroke. Perfect Freehand
  generates the outline, joined with a closed quadratic spline. Sparse segments
  get interpolated support points to keep corner rounding local
  and preserve pressure on two-point lines. Pressure is mapped to the existing
  0.35..1 width range; velocity-based simulation is disabled. The highlighter does not
  taper: it is a flat band, because a tapering edge reads as a smudge rather
  than a marker.

- **Painting ink onto a canvas is written once** (`src/lib/inkRaster.ts`'s
  `paintStrokes`), and has two callers: Paper's page snapshot
  (`PaperSurface.tsx`) and the newspaper reader's Journal thumbnails
  (`NewspaperReader.tsx`). It fills the _same_ path data the screen is drawn
  with, so a picture cannot drift from what was on the page, and it owns the
  three decisions that turn a path into paint — the colour a stroke resolves
  to, the highlighter's alpha, and doing nothing rather than crashing where
  `Path2D` does not exist (jsdom has none). The caller owns the transform: only
  it knows what units its strokes are in.

- **Preview and release use identical geometry.** Both simplify the captured
  points with the same bounded, shape- and pressure-aware reducer before calling
  the renderer, which explicitly uses `last: true` in both states. Never put a
  more aggressive reduction only on pointer-up: it changes what was drawn as
  soon as the pen lifts. Raster tests check filled joins and pressure widths;
  component tests compare the live path with the committed and reloaded path.

- **The stroke in flight is its own element, written straight to the DOM.** A
  React render per pointer move would re-derive every other stroke's outline on
  the page; instead one attribute on one node changes, coalesced to one rebuild
  per animation frame. This is also why a whole class of bug is simply gone: on
  the canvas, in-progress ink was painted onto the bitmap and was not in the
  committed state yet, so _any_ repaint — a picture finishing its upload, say —
  erased what had just been written until the stroke ended. Committed strokes
  are memoised on the stroke object, which is immutable, so erasing re-derives
  only the strokes it actually cut.

- **The eraser is geometric and lays down nothing.** It splits the strokes it
  crosses into the runs that survive, and is itself discarded, so there is no
  "paint over it in the page colour" anywhere. That is not a detail: it is
  exactly what lets the newspaper's ink layer be transparent over a PDF page. A
  scrub that crosses nothing is not an edit and costs no undo step — measured in
  **points, not strokes**: rubbing the tail off a stroke leaves it one stroke
  exactly as rubbing nothing does, so comparing stroke counts silently discarded
  those.

- **It is not an SVG `<mask>`, and that is on purpose.** Masking would render
  correctly, transparency included, but strokes here are _persisted_ rather than
  only painted: a mask is a rendering artifact, so surviving a reload would mean
  storing the eraser strokes too — and erasing would then _grow_ the document
  instead of shrinking it. The newspaper has a hard server-side budget (10,000
  strokes / 100,000 points, `backend/newspapers/issues.py`), so rubbing
  something out would cost the same budget as drawing it, and every erase would
  add a mask to composite against the ink forever. Neither export path could use
  one either: the reader's `exportPdf` draws segments through pdf-lib and
  Paper's snapshot fills path data through `Path2D`, so both want geometry and
  the splitting code would have to exist anyway, as a second representation that
  has to agree with the first. The _result_ is SVG — survivors are just paths.
  Only the operation is geometric.

- **`useInkTouchPolicy` names the one thing the surfaces genuinely disagree
  about, rather than splitting the difference.** It follows from whether there
  is anything underneath to scroll, and the wrong choice breaks a surface
  outright:
  - `exclusive` — the surface _is_ the screen (Paper, and the Study desk's
    page). Nothing scrolls, so `touch-action: none`, and a finger is free to
    mean something else: a swipe flips the page, a two-finger tap toggles the
    eraser (Apple Pencil's own double-tap and squeeze are exposed to no browser
    API, which is why an on-canvas gesture exists at all).
  - `scroll` — the surface is drawn over a scrolling column (the newspaper). A
    finger belongs to the browser in every tool, so it keeps native momentum
    scrolling and pinch-zoom, and the app claims **no** gestures.

  Under `scroll` the Pencil is held off the scroller by a **native, non-passive
  `touchmove` listener**, and every part of that sentence was paid for:
  `touch-action` cannot tell a pen from a finger (and WebKit ignores it outright
  on an `<svg>`, which this is), `preventDefault` on `pointerdown` does not stop
  an iPadOS scroll, once that scroll starts the pen pointer is _cancelled_
  mid-stroke — which is what "the Pencil scrolls instead of writing" was — and
  React attaches `touchmove` passively, so a prop cannot do it. The listener
  goes on the **page box, not the ink layer**: on a scrolling surface the ink
  layer is mounted only while its page is near the viewport, and the guard has
  to outlive that.

- **A cancelled stroke splits the same way**, and deliberately is not unified.
  Under `exclusive` it is committed: the ink was drawn, is on screen, and a
  cancel there is a quirk. Under `scroll` it is discarded: a cancel means the OS
  took the pointer to scroll with, and half a stray line dragged across a
  photograph is worse than no line at all. `onPointerLeave` ends a stroke only
  under `exclusive`, because a scrolling column moves under the pointer as a
  matter of course.

- **Colour is optional on the stroke and resolved against the surface's palette
  at paint time.** This is the only arrangement in which both histories keep the
  colour they were drawn in: Paper's ink has always been black, the newspaper's
  blue, and neither has a colour stored on a single existing stroke. A
  parse-time default would have to pick one and silently restyle the other. Each
  tool remembers its own colour the way it already remembers its own width.

- **`InkToolPanel` floats and snaps to an edge, carries no transient text, and
  every control is a fixed-size square.** Both rules exist because the tools once
  shared the top bar with the save indicator: "Saving…" popped in and out, the
  bar reflowed, and the buttons moved out from under a stylus aiming for them.
  So the width row is always three buttons and the colour row always four
  whatever the tool — the eraser has no colour, but hiding its swatches would
  resize the panel on a tool switch, which is the exact failure being prevented.
  Placement is remembered per surface: where the panel belongs over an A4 sheet
  is not where it belongs over a broadsheet.

- **Paper still rasterizes, and only Paper.** A page's PNG snapshot is
  load-bearing — `backend/routes/paper.py` serves it as the explorer grid's
  cover, the Journal filmstrip's thumbnails and the Ideas sketch picker — so
  `PaperSurface` renders one at save time. It fills the _same_ path data the
  page is drawn with onto an offscreen canvas via `Path2D`, so the thumbnail
  cannot drift from what is on screen. Serializing the `<svg>` and loading it
  through an `<img>` would have been shorter and would have silently dropped
  every picture: an SVG rasterized that way is not allowed to fetch its own
  `href`s. The snapshot is a fixed 1240px wide, where it used to be whatever the
  canvas happened to be on screen — the same page produced a different
  thumbnail on a laptop and on a phone.
