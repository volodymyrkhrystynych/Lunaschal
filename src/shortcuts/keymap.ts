// Shortcut keymap: action ids, default bindings, and combo helpers.
// Combos use browser KeyboardEvent.code with optional lowercase modifiers,
// e.g. "KeyW", "shift+KeyN". This is separate from the evdev key names used
// by the OS-level STT listener.

export type ActionId =
  | 'nav.up'
  | 'nav.down'
  | 'nav.out'
  | 'nav.in'
  | 'action.new'
  | 'action.newAlt'
  | 'action.annotate'
  | 'action.search'
  | 'tab.chat'
  | 'tab.tasks'
  | 'tab.journal'
  | 'tab.writing'
  | 'tab.calendar'
  | 'tab.learning'
  | 'tab.cookbook'
  | 'tab.fanfic'
  | 'tab.files'
  | 'tab.notebook'
  | 'tab.settings'
  | 'global.newJournalEntry'
  | 'global.toggleSidebar'
  | 'learning.approve'
  | 'learning.deny'
  | 'learning.record'
  | 'learning.check'
  | 'learning.flip'
  | 'learning.rate1'
  | 'learning.rate2'
  | 'learning.rate3'
  | 'learning.rate4'
  | 'reader.fontUp'
  | 'reader.fontDown'
  | 'reader.toggleList';

export const DEFAULT_BINDINGS: Record<ActionId, string> = {
  'nav.up': 'KeyW',
  'nav.down': 'KeyS',
  'nav.out': 'KeyA',
  'nav.in': 'KeyD',
  'action.new': 'KeyN',
  'action.newAlt': 'shift+KeyN',
  'action.annotate': 'KeyI',
  'action.search': 'KeyF',
  // Tab digits are unbound by default — the number row belongs to Learning
  // review ratings. Rebindable in Settings ('' = unbound).
  'tab.chat': '',
  'tab.tasks': '',
  'tab.journal': '',
  'tab.writing': '',
  'tab.calendar': '',
  'tab.learning': '',
  'tab.cookbook': '',
  'tab.fanfic': '',
  'tab.files': '',
  'tab.notebook': '',
  'tab.settings': '',
  'global.newJournalEntry': 'shift+KeyJ',
  'global.toggleSidebar': 'KeyB',
  'learning.approve': 'KeyY',
  'learning.deny': 'KeyX',
  'learning.record': 'KeyV',
  'learning.check': 'Enter',
  'learning.flip': 'Space',
  'learning.rate1': 'Digit1',
  'learning.rate2': 'Digit2',
  'learning.rate3': 'Digit3',
  'learning.rate4': 'Digit4',
  'reader.fontUp': 'Equal',
  'reader.fontDown': 'Minus',
  'reader.toggleList': 'KeyL',
};

export const ACTION_LABELS: Record<ActionId, string> = {
  'nav.up': 'Move up / previous item / scroll',
  'nav.down': 'Move down / next item / scroll',
  'nav.out': 'Go out (back toward sidebar)',
  'nav.in': 'Go in (drill into tab / item)',
  'action.new': 'New item (entry, file, project…)',
  'action.newAlt': 'New folder (Files)',
  'action.annotate':
    'Write commentary (reader) / steer regeneration (Learning queue)',
  'action.search': 'Focus search (Library)',
  'tab.chat': 'Go to Chat',
  'tab.tasks': 'Go to Tasks',
  'tab.journal': 'Go to Journal',
  'tab.writing': 'Go to Writing',
  'tab.calendar': 'Go to Calendar',
  'tab.learning': 'Go to Learning',
  'tab.cookbook': 'Go to Cookbook',
  'tab.fanfic': 'Go to Library',
  'tab.files': 'Go to Files',
  'tab.notebook': 'Go to Notebook',
  'tab.settings': 'Go to Settings',
  'global.newJournalEntry': 'New journal entry (from anywhere)',
  'global.toggleSidebar': 'Toggle sidebar (open/close)',
  'learning.approve': 'Approve selected card (Learning queue)',
  'learning.deny': 'Deny selected card (Learning queue)',
  'learning.record': 'Toggle voice recording (Learning)',
  'learning.check': 'Check answer (Learning review)',
  'learning.flip': 'Flip card / show answer (Learning review)',
  'learning.rate1': 'Rate Again (Learning review)',
  'learning.rate2': 'Rate Hard (Learning review)',
  'learning.rate3': 'Rate Good (Learning review)',
  'learning.rate4': 'Rate Easy (Learning review)',
  'reader.fontUp': 'Increase reading/chapter text size',
  'reader.fontDown': 'Decrease reading/chapter text size',
  'reader.toggleList':
    'Toggle list panel (Writing chapters / Library chapters)',
};

const MODIFIER_CODES = new Set([
  'ControlLeft',
  'ControlRight',
  'AltLeft',
  'AltRight',
  'ShiftLeft',
  'ShiftRight',
  'MetaLeft',
  'MetaRight',
]);

export function isModifierCode(code: string): boolean {
  return MODIFIER_CODES.has(code);
}

export function comboFromEvent(e: KeyboardEvent): string {
  const parts: string[] = [];
  if (e.ctrlKey) parts.push('ctrl');
  if (e.altKey) parts.push('alt');
  if (e.shiftKey) parts.push('shift');
  if (e.metaKey) parts.push('meta');
  parts.push(e.code);
  return parts.join('+');
}

export function displayCombo(combo: string): string {
  return combo
    .split('+')
    .map(part => {
      if (part === 'ctrl') return 'Ctrl';
      if (part === 'alt') return 'Alt';
      if (part === 'shift') return 'Shift';
      if (part === 'meta') return 'Meta';
      if (part.startsWith('Key')) return part.slice(3);
      if (part.startsWith('Digit')) return part.slice(5);
      if (part.startsWith('Arrow')) return part.slice(5) + ' Arrow';
      if (part === 'Equal') return '=';
      if (part === 'Minus') return '-';
      return part;
    })
    .join(' + ');
}

export function isEditableTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return (
    tag === 'INPUT' ||
    tag === 'TEXTAREA' ||
    tag === 'SELECT' ||
    el.isContentEditable
  );
}

// Set to true while a key-recorder is listening so the global handler stays quiet.
export const keyCapture = { active: false };
