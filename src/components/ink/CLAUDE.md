# The ink layer (`src/components/ink/`, `src/lib/ink.ts`, `src/lib/inkPanel.ts`)

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
  declares, and the transform to the screen is always a single uniform scale —
  the separate x/y scales that preceded it are what used to distort saved ink
  when a window changed shape. Page-specific geometry lives beside the surface
  that owns it: `src/lib/paper.ts` for the A4 sheet, `src/lib/newspaperMarkup.ts`
  for a PDF page.

- **`InkCanvas` owns the pointer loop and the painting**, and the parts of it
  that look incidental are not. Coalesced events are drained on every move (a
  pen reports faster than the browser fires, and dropping the batch is what
  makes a fast stroke a polygon); opaque tools paint only the new tail segment,
  which is what keeps ink feeling attached to the nib; the highlighter is one
  translucent pass so overlapping segments don't stack alpha into dark blobs;
  and two effects bail while a stroke is in flight, because in-progress ink is
  painted straight to the canvas and is not in `stateRef` yet — a repaint
  landing mid-stroke erases everything drawn so far.

- **The eraser is geometric and lays down nothing.** It splits the strokes it
  crosses and is itself discarded, so there is no "paint over it in the page
  colour" anywhere in the paint path. That is not a detail: it is exactly what
  lets the newspaper's ink layer be transparent over a PDF page. Anyone
  optimising the eraser preview into a fill will break the newspaper silently,
  and `InkCanvas.test.tsx` pins it. A scrub that crosses nothing is not an edit
  and costs no undo step.

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
  `touch-action` cannot tell a pen from a finger (and WebKit ignored it outright
  on the `<svg>` this used to be), `preventDefault` on `pointerdown` does not
  stop an iPadOS scroll, once that scroll starts the pen pointer is _cancelled_
  mid-stroke — which is what "the Pencil scrolls instead of writing" was — and
  React attaches `touchmove` passively, so a prop cannot do it. The listener
  goes on the **page box, not the canvas**: on a scrolling surface the canvas is
  mounted only while its page is near the viewport, and the guard has to outlive
  that.

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

- **On a scrolling surface the ink canvas is mounted only near the viewport.**
  A broadsheet canvas is tens of megabytes at full pixel ratio and an issue can
  run to hundreds of pages. This is safe there and only there: the newspaper's
  strokes live in the reader's markup, so the canvas is a view that can be
  rebuilt at will, whereas Paper's bitmap _is_ the artifact — it is what
  `getSaveData` turns into the page snapshot. The laziness belongs to the
  newspaper's `Page`, not to `InkCanvas`, and must not leak into Paper.

- **One loss, taken knowingly.** The newspaper's ink was SVG, which stays crisp
  under pinch-zoom; a canvas bitmap does not. It is a bitmap over a bitmap now —
  the pdf.js canvas beneath is already capped at 2048px and blurs on zoom by the
  same factor, so the ink blurs _with_ the page rather than floating sharp over
  soft text. Raising the ink canvas's backing store on zoom would undo the
  memory budget above, so it deliberately does not.
