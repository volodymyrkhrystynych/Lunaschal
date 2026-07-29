import { useState, useEffect, useRef } from 'react';
import { keyCapture } from '../../shortcuts/keymap';

// Maps browser KeyboardEvent.code → evdev keycode name
const CODE_TO_EVDEV: Record<string, string> = (() => {
  const m: Record<string, string> = {
    F1: 'KEY_F1',
    F2: 'KEY_F2',
    F3: 'KEY_F3',
    F4: 'KEY_F4',
    F5: 'KEY_F5',
    F6: 'KEY_F6',
    F7: 'KEY_F7',
    F8: 'KEY_F8',
    F9: 'KEY_F9',
    F10: 'KEY_F10',
    F11: 'KEY_F11',
    F12: 'KEY_F12',
    AltLeft: 'KEY_LEFTALT',
    AltRight: 'KEY_RIGHTALT',
    ControlLeft: 'KEY_LEFTCTRL',
    ControlRight: 'KEY_RIGHTCTRL',
    ShiftLeft: 'KEY_LEFTSHIFT',
    ShiftRight: 'KEY_RIGHTSHIFT',
    MetaLeft: 'KEY_LEFTMETA',
    MetaRight: 'KEY_RIGHTMETA',
    CapsLock: 'KEY_CAPSLOCK',
    Insert: 'KEY_INSERT',
    Delete: 'KEY_DELETE',
    Home: 'KEY_HOME',
    End: 'KEY_END',
    PageUp: 'KEY_PAGEUP',
    PageDown: 'KEY_PAGEDOWN',
    ScrollLock: 'KEY_SCROLLLOCK',
    Pause: 'KEY_PAUSE',
    PrintScreen: 'KEY_SYSRQ',
    NumLock: 'KEY_NUMLOCK',
    Backquote: 'KEY_GRAVE',
    Backslash: 'KEY_BACKSLASH',
  };
  for (const c of 'ABCDEFGHIJKLMNOPQRSTUVWXYZ') m[`Key${c}`] = `KEY_${c}`;
  for (const d of '0123456789') m[`Digit${d}`] = `KEY_${d}`;
  return m;
})();

const EVDEV_DISPLAY: Record<string, string> = (() => {
  const m: Record<string, string> = {
    KEY_F1: 'F1',
    KEY_F2: 'F2',
    KEY_F3: 'F3',
    KEY_F4: 'F4',
    KEY_F5: 'F5',
    KEY_F6: 'F6',
    KEY_F7: 'F7',
    KEY_F8: 'F8',
    KEY_F9: 'F9',
    KEY_F10: 'F10',
    KEY_F11: 'F11',
    KEY_F12: 'F12',
    KEY_LEFTALT: 'Left Alt',
    KEY_RIGHTALT: 'Right Alt',
    KEY_LEFTCTRL: 'Left Ctrl',
    KEY_RIGHTCTRL: 'Right Ctrl',
    KEY_LEFTSHIFT: 'Left Shift',
    KEY_RIGHTSHIFT: 'Right Shift',
    KEY_LEFTMETA: 'Left Meta',
    KEY_RIGHTMETA: 'Right Meta',
    KEY_CAPSLOCK: 'Caps Lock',
    KEY_INSERT: 'Insert',
    KEY_DELETE: 'Delete',
    KEY_HOME: 'Home',
    KEY_END: 'End',
    KEY_PAGEUP: 'Page Up',
    KEY_PAGEDOWN: 'Page Down',
    KEY_SCROLLLOCK: 'Scroll Lock',
    KEY_PAUSE: 'Pause',
    KEY_SYSRQ: 'Print Screen',
    KEY_NUMLOCK: 'Num Lock',
    KEY_GRAVE: 'Backtick',
    KEY_BACKSLASH: 'Backslash',
  };
  for (const c of 'ABCDEFGHIJKLMNOPQRSTUVWXYZ') m[`KEY_${c}`] = c;
  for (const d of '0123456789') m[`KEY_${d}`] = d;
  return m;
})();

// Modifier keys that can be held as part of a combo
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

// Canonical sort order for modifiers when building combo strings
const MOD_PRIORITY: Record<string, number> = {
  KEY_LEFTCTRL: 0,
  KEY_RIGHTCTRL: 1,
  KEY_LEFTALT: 2,
  KEY_RIGHTALT: 3,
  KEY_LEFTSHIFT: 4,
  KEY_RIGHTSHIFT: 5,
  KEY_LEFTMETA: 6,
  KEY_RIGHTMETA: 7,
};

function displayCombo(
  evdev: string | null | undefined,
  fallback: string
): string {
  if (!evdev) return fallback;
  return evdev
    .split('+')
    .map(k => EVDEV_DISPLAY[k] ?? k)
    .join(' + ');
}

export function KeyRecorder({
  value,
  onChange,
}: {
  value: string | null;
  onChange: (key: string) => void;
}) {
  const [listening, setListening] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);
  const heldRef = useRef<Set<string>>(new Set());
  const modTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!listening) {
      heldRef.current = new Set();
      return;
    }
    keyCapture.active = true;

    const done = (combo: string) => {
      if (modTimerRef.current) {
        clearTimeout(modTimerRef.current);
        modTimerRef.current = null;
      }
      onChange(combo);
      setListening(false);
      heldRef.current = new Set();
    };

    const handleDown = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();

      if (MODIFIER_CODES.has(e.code)) {
        heldRef.current.add(e.code);
        // Wait briefly for a non-modifier key; if none comes, record modifier(s) alone
        if (modTimerRef.current) clearTimeout(modTimerRef.current);
        modTimerRef.current = setTimeout(() => {
          modTimerRef.current = null;
          const parts = [...heldRef.current]
            .map(m => CODE_TO_EVDEV[m])
            .filter(Boolean) as string[];
          parts.sort(
            (a, b) => (MOD_PRIORITY[a] ?? 99) - (MOD_PRIORITY[b] ?? 99)
          );
          if (parts.length > 0) done(parts.join('+'));
        }, 500);
        return;
      }

      // Non-modifier key: record modifiers currently held + this key immediately
      if (modTimerRef.current) {
        clearTimeout(modTimerRef.current);
        modTimerRef.current = null;
      }
      const evdev = CODE_TO_EVDEV[e.code];
      if (!evdev) return;
      const modParts = [...heldRef.current]
        .map(m => CODE_TO_EVDEV[m])
        .filter(Boolean) as string[];
      modParts.sort(
        (a, b) => (MOD_PRIORITY[a] ?? 99) - (MOD_PRIORITY[b] ?? 99)
      );
      done([...modParts, evdev].join('+'));
    };

    const handleUp = (e: KeyboardEvent) => {
      heldRef.current.delete(e.code);
    };

    window.addEventListener('keydown', handleDown, true);
    window.addEventListener('keyup', handleUp, true);
    return () => {
      window.removeEventListener('keydown', handleDown, true);
      window.removeEventListener('keyup', handleUp, true);
      if (modTimerRef.current) {
        clearTimeout(modTimerRef.current);
        modTimerRef.current = null;
      }
      keyCapture.active = false;
    };
  }, [listening, onChange]);

  useEffect(() => {
    if (listening) ref.current?.focus();
  }, [listening]);

  return (
    <button
      ref={ref}
      onClick={() => setListening(true)}
      onBlur={() => setListening(false)}
      className={`px-3 py-1.5 rounded text-sm border transition-colors ${
        listening
          ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)] animate-pulse'
          : 'border-white/20 bg-white/5 hover:bg-white/10 text-[var(--color-text)]'
      }`}
    >
      {listening ? 'Press a key combo…' : displayCombo(value, 'Not set')}
    </button>
  );
}
