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
  | 'tab.chat'
  | 'tab.tasks'
  | 'tab.journal'
  | 'tab.writing'
  | 'tab.calendar'
  | 'tab.flashcards'
  | 'tab.cookbook'
  | 'tab.files'
  | 'tab.settings'
  | 'global.newJournalEntry';

export const DEFAULT_BINDINGS: Record<ActionId, string> = {
  'nav.up': 'KeyW',
  'nav.down': 'KeyS',
  'nav.out': 'KeyA',
  'nav.in': 'KeyD',
  'action.new': 'KeyN',
  'action.newAlt': 'shift+KeyN',
  'tab.chat': 'Digit1',
  'tab.tasks': 'Digit2',
  'tab.journal': 'Digit3',
  'tab.writing': 'Digit4',
  'tab.calendar': 'Digit5',
  'tab.flashcards': 'Digit6',
  'tab.cookbook': 'Digit9',
  'tab.files': 'Digit7',
  'tab.settings': 'Digit8',
  'global.newJournalEntry': 'shift+KeyJ',
};

export const ACTION_LABELS: Record<ActionId, string> = {
  'nav.up': 'Move up / previous item',
  'nav.down': 'Move down / next item',
  'nav.out': 'Go out (back toward sidebar)',
  'nav.in': 'Go in (drill into tab / item)',
  'action.new': 'New item (entry, file, project…)',
  'action.newAlt': 'New folder (Files)',
  'tab.chat': 'Go to Chat',
  'tab.tasks': 'Go to Tasks',
  'tab.journal': 'Go to Journal',
  'tab.writing': 'Go to Writing',
  'tab.calendar': 'Go to Calendar',
  'tab.flashcards': 'Go to Flashcards',
  'tab.cookbook': 'Go to Cookbook',
  'tab.files': 'Go to Files',
  'tab.settings': 'Go to Settings',
  'global.newJournalEntry': 'New journal entry (from anywhere)',
};

const MODIFIER_CODES = new Set([
  'ControlLeft', 'ControlRight', 'AltLeft', 'AltRight',
  'ShiftLeft', 'ShiftRight', 'MetaLeft', 'MetaRight',
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
    .map((part) => {
      if (part === 'ctrl') return 'Ctrl';
      if (part === 'alt') return 'Alt';
      if (part === 'shift') return 'Shift';
      if (part === 'meta') return 'Meta';
      if (part.startsWith('Key')) return part.slice(3);
      if (part.startsWith('Digit')) return part.slice(5);
      if (part.startsWith('Arrow')) return part.slice(5) + ' Arrow';
      return part;
    })
    .join(' + ');
}

export function isEditableTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

// Set to true while a key-recorder is listening so the global handler stays quiet.
export const keyCapture = { active: false };
