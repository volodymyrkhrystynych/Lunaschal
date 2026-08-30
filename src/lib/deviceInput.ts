/**
 * What kind of device is holding the app, for the few places where the answer
 * changes what is worth rendering.
 *
 * Kept here rather than inline so it can be tested in the node environment
 * (see CLAUDE.md — that is what src/lib is for) and so there is one definition
 * of "phone" rather than a media query copied into every component.
 */

/**
 * True on a touch device — a phone or tablet, not a desktop with a mouse.
 *
 * `pointer: coarse` describes the *primary* pointer, which is the question
 * being asked: a laptop with a touchscreen still has a mouse and should get the
 * desktop affordances.
 *
 * Used to decide whether to offer a camera button at all. `<input capture>`
 * opens the camera on iOS and Android and is silently ignored everywhere else,
 * so on a desktop it would be a second, identical file dialog wearing a camera
 * icon — a button that lies about what it does.
 *
 * Defensive about `matchMedia` because this also runs under jsdom and inside
 * the PyWebView shell, neither of which is guaranteed to implement it.
 */
export function isTouchDevice(): boolean {
  try {
    return window.matchMedia?.('(pointer: coarse)').matches ?? false;
  } catch {
    return false;
  }
}
