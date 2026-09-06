import { describe, it, expect } from 'vitest';
import { isViewAvailable, visibleNavItems } from './navVisibility';
import { navItems } from '../components/Sidebar';
import { VIEW_ORDER } from '../shortcuts/ShortcutProvider';
import { VIEWS } from './viewPersistence';

const DESKTOP = { isDesktopShell: true };
const BROWSER = { isDesktopShell: false };

const viewsOn = (gates: { isDesktopShell: boolean }) =>
  visibleNavItems(navItems, gates).map(item => item.view);

describe('the one device gate', () => {
  it('keeps an ungated view everywhere', () => {
    for (const gates of [DESKTOP, BROWSER]) {
      expect(viewsOn(gates)).toContain('chat');
    }
  });

  it('gates Piano on the native shell', () => {
    expect(viewsOn(DESKTOP)).toContain('piano');
    // A browser tab is not the shell, however big the monitor.
    expect(viewsOn(BROWSER)).not.toContain('piano');
  });

  it('does not gate Study at all any more', () => {
    // It used to be hidden below 1024px, which took the import queue away
    // with the reading desk. The tab is everywhere now and narrows itself —
    // the size decision lives in Study.tsx, not in this list.
    expect(viewsOn(DESKTOP)).toContain('study');
    expect(viewsOn(BROWSER)).toContain('study');
  });
});

// Adding a top-level view means editing three hand-maintained lists in three
// files. A view missing from VIEW_ORDER simply can't be reached by the
// keyboard, and one whose position differs makes nav.up/down disagree with the
// visible tab order — both silent. `repo_facts.view_facts` warns about this
// drift after the fact; this fails the build for it.
describe('the three view registries agree', () => {
  it('VIEW_ORDER is navItems in the same order', () => {
    expect(VIEW_ORDER).toEqual(navItems.map(item => item.view));
  });

  it('VIEWS covers exactly the views the sidebar offers', () => {
    expect([...VIEWS].sort()).toEqual(navItems.map(i => i.view).sort());
  });
});

describe('isViewAvailable', () => {
  it('refuses an item that needs the shell outside it', () => {
    expect(isViewAvailable({ desktopOnly: true }, BROWSER)).toBe(false);
    expect(isViewAvailable({ desktopOnly: true }, DESKTOP)).toBe(true);
    expect(isViewAvailable({}, BROWSER)).toBe(true);
  });
});
