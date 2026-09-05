import { describe, it, expect } from 'vitest';
import { isViewAvailable, visibleNavItems } from './navVisibility';
import { navItems } from '../components/Sidebar';
import { VIEW_ORDER } from '../shortcuts/ShortcutProvider';
import { VIEWS } from './viewPersistence';

const DESKTOP_BIG = { isDesktopShell: true, isLargeScreen: true };
const DESKTOP_SMALL = { isDesktopShell: true, isLargeScreen: false };
const BROWSER_BIG = { isDesktopShell: false, isLargeScreen: true };
const PHONE = { isDesktopShell: false, isLargeScreen: false };

const viewsOn = (gates: { isDesktopShell: boolean; isLargeScreen: boolean }) =>
  visibleNavItems(navItems, gates).map(item => item.view);

describe('the two device gates are independent', () => {
  it('keeps an ungated view everywhere', () => {
    for (const gates of [DESKTOP_BIG, DESKTOP_SMALL, BROWSER_BIG, PHONE]) {
      expect(viewsOn(gates)).toContain('chat');
    }
  });

  it('gates Piano on the native shell, not on size', () => {
    // The Pocket 2 runs the desktop app on a small screen: Piano stays.
    expect(viewsOn(DESKTOP_SMALL)).toContain('piano');
    // A browser tab on a big monitor is not the shell: Piano is gone.
    expect(viewsOn(BROWSER_BIG)).not.toContain('piano');
  });

  it('gates Study on size, not on the native shell', () => {
    // This is the case the whole gate exists for: the desktop app on the
    // Pocket 2 satisfies `desktopOnly` and must still not offer Study.
    expect(viewsOn(DESKTOP_SMALL)).not.toContain('study');
    expect(viewsOn(PHONE)).not.toContain('study');
    // A wide browser tab (or a 12" iPad) gets it without the native shell.
    expect(viewsOn(BROWSER_BIG)).toContain('study');
    expect(viewsOn(DESKTOP_BIG)).toContain('study');
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
  it('refuses an item failing either gate', () => {
    expect(isViewAvailable({ largeOnly: true }, DESKTOP_SMALL)).toBe(false);
    expect(isViewAvailable({ desktopOnly: true }, BROWSER_BIG)).toBe(false);
    expect(
      isViewAvailable({ desktopOnly: true, largeOnly: true }, DESKTOP_BIG)
    ).toBe(true);
  });
});
