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
  'ideas',
  'food',
  'lifestyle',
  'fanfic',
  'newspapers',
  'paper',
  'email',
  'practice',
] as const;

export type View = (typeof VIEWS)[number];

function isView(value: string | null): value is View {
  return VIEWS.includes(value as View);
}

// Views that no longer exist, and where their contents went. Without this a
// retired name fails `isView` and the reload lands on the default 'chat' —
// which, for someone whose last screen was Tasks, looks like the app forgot.
const RETIRED_VIEWS: Record<string, View> = {
  tasks: 'lifestyle', // daily tasks and to-dos live in Lifestyle now
};

export function getStoredView(): View | null {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (raw && raw in RETIRED_VIEWS) return RETIRED_VIEWS[raw];
  return isView(raw) ? raw : null;
}

export function setStoredView(view: View): void {
  localStorage.setItem(STORAGE_KEY, view);
}
