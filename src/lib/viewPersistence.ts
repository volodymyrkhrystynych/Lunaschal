// Remembers which sidebar view was open. Lunaschal runs as an installed
// standalone PWA (see public/manifest.json); mobile OSes reclaim a backgrounded
// webview's memory on screen-off and re-execute the whole app on the next
// screen-on, wiping React state. Without this, that reload always lands back
// on the default 'chat' view instead of wherever the user actually was.
const STORAGE_KEY = 'lunaschal:currentView';

export const VIEWS = [
  'chat',
  'journal',
  'meetings',
  'calendar',
  'learning',
  'settings',
  'files',
  'notebook',
  'writing',
  'tasks',
  'food',
  'lifestyle',
  'fanfic',
  'newspapers',
  'paper',
  'email',
] as const;

export type View = (typeof VIEWS)[number];

function isView(value: string | null): value is View {
  return VIEWS.includes(value as View);
}

export function getStoredView(): View | null {
  const raw = localStorage.getItem(STORAGE_KEY);
  return isView(raw) ? raw : null;
}

export function setStoredView(view: View): void {
  localStorage.setItem(STORAGE_KEY, view);
}
