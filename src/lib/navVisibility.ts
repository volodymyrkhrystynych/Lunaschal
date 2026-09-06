// Which top-level views this device is allowed to show. Pure so it can be
// tested in the node environment, and shared so the sidebar's rendered list and
// the shortcut cycle's `availableViews` can never disagree — they were two
// copies of the same filter expression, and a third caller was about to make it
// three.
//
// There is exactly one gate, and it is about the *shell*, not the screen:
//   desktopOnly — running inside the PyWebView window (Piano needs the native
//                 shell), true on any machine that runs the app, tiny ones too.
//
// There used to be a second, `largeOnly`, which hid Study below 1024px. It is
// gone on purpose: a tab that vanishes on the phone cannot be used to *queue*
// anything, and importing a video is exactly the half of Study that wants
// doing from wherever you found the link. Study now exists everywhere and
// shows less where there is less room — the size question belongs inside a
// view, not to the list of views. See src/components/Study/Study.tsx.

export interface NavGateable {
  desktopOnly?: boolean;
}

export interface DeviceGates {
  isDesktopShell: boolean;
}

export function isViewAvailable(
  item: NavGateable,
  gates: DeviceGates
): boolean {
  if (item.desktopOnly && !gates.isDesktopShell) return false;
  return true;
}

export function visibleNavItems<T extends NavGateable>(
  items: readonly T[],
  gates: DeviceGates
): T[] {
  return items.filter(item => isViewAvailable(item, gates));
}
