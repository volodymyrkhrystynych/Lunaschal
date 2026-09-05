// Which top-level views this device is allowed to show. Pure so it can be
// tested in the node environment, and shared so the sidebar's rendered list and
// the shortcut cycle's `availableViews` can never disagree — they were two
// copies of the same filter expression, and a third caller was about to make it
// three.
//
// The two gates mean different things and neither implies the other:
//   desktopOnly — running inside the PyWebView window (Piano needs the native
//                 shell), true on any machine that runs the app, tiny ones too.
//   largeOnly   — the viewport is wide enough for the view to work at all
//                 (Study's two panes), true of a browser tab on a big monitor
//                 and false of the GPD Pocket 2 running that same native shell.

export interface NavGateable {
  desktopOnly?: boolean;
  largeOnly?: boolean;
}

export interface DeviceGates {
  isDesktopShell: boolean;
  isLargeScreen: boolean;
}

export function isViewAvailable(
  item: NavGateable,
  gates: DeviceGates
): boolean {
  if (item.desktopOnly && !gates.isDesktopShell) return false;
  if (item.largeOnly && !gates.isLargeScreen) return false;
  return true;
}

export function visibleNavItems<T extends NavGateable>(
  items: readonly T[],
  gates: DeviceGates
): T[] {
  return items.filter(item => isViewAvailable(item, gates));
}
