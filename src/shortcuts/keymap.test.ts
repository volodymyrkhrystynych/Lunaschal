import { describe, it, expect } from 'vitest';
import { DEFAULT_BINDINGS, displayCombo, isModifierCode } from './keymap';

describe('DEFAULT_BINDINGS', () => {
  it('binds the vim-style reader keys', () => {
    expect(DEFAULT_BINDINGS['scroll.down']).toBe('KeyJ');
    expect(DEFAULT_BINDINGS['scroll.up']).toBe('KeyK');
    expect(DEFAULT_BINDINGS['action.annotate']).toBe('KeyI');
  });

  it('has no two actions on the same combo', () => {
    const combos = Object.values(DEFAULT_BINDINGS);
    expect(new Set(combos).size).toBe(combos.length);
  });
});

describe('displayCombo', () => {
  it('humanizes modifier tokens', () => {
    expect(displayCombo('ctrl+alt+KeyS')).toBe('Ctrl + Alt + S');
  });

  it('strips the Key/Digit prefixes', () => {
    expect(displayCombo('KeyA')).toBe('A');
    expect(displayCombo('Digit5')).toBe('5');
  });

  it('labels arrow keys', () => {
    expect(displayCombo('ArrowUp')).toBe('Up Arrow');
  });

  it('passes through unknown tokens unchanged', () => {
    expect(displayCombo('Space')).toBe('Space');
  });
});

describe('isModifierCode', () => {
  it('recognizes left/right modifier codes', () => {
    expect(isModifierCode('ControlLeft')).toBe(true);
    expect(isModifierCode('AltRight')).toBe(true);
    expect(isModifierCode('ShiftLeft')).toBe(true);
    expect(isModifierCode('MetaRight')).toBe(true);
  });

  it('returns false for non-modifier keys', () => {
    expect(isModifierCode('KeyA')).toBe(false);
    expect(isModifierCode('Space')).toBe(false);
  });
});
