// @vitest-environment jsdom
import { StrictMode } from 'react';
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useDraftState } from './useDraftState';

describe('durable text drafts', () => {
  it('recovers the last keystroke without timers, effects or unload events', () => {
    const first = renderHook(() => useDraftState('entry', ''), {
      wrapper: StrictMode,
    });
    act(() => {
      first.result.current[1]('Transcript');
      first.result.current[1](text => text + ' corrected');
      expect(localStorage.getItem('lunaschal:draft:v1:entry')).toBe(
        '"Transcript corrected"'
      );
    });
    first.unmount();
    const second = renderHook(() => useDraftState('entry', ''));
    expect(second.result.current[0]).toBe('Transcript corrected');
    act(() => second.result.current[1](''));
    second.unmount();
    expect(renderHook(() => useDraftState('entry', '')).result.current[0]).toBe(
      ''
    );
  });

  it('isolates chapters and ignores an old chapter callback for the visible state', () => {
    const hook = renderHook(({ id }) => useDraftState(id, ''), {
      initialProps: { id: 'a' },
    });
    const setA = hook.result.current[1];
    act(() => setA('Chapter A'));
    hook.rerender({ id: 'b' });
    expect(hook.result.current[0]).toBe('');
    act(() => hook.result.current[1]('Chapter B'));
    act(() => setA(''));
    expect(hook.result.current[0]).toBe('Chapter B');
    hook.rerender({ id: 'a' });
    expect(hook.result.current[0]).toBe('');
    hook.rerender({ id: 'b' });
    expect(hook.result.current[0]).toBe('Chapter B');
  });

  it('ignores corrupt or incompatible stored data', () => {
    for (const bad of ['{', 'null', '42', '{"title":4}']) {
      localStorage.setItem('lunaschal:draft:v1:bad', bad);
      const hook = renderHook(() =>
        useDraftState('bad', { title: '', tags: [] as string[] })
      );
      expect(hook.result.current[0]).toEqual({ title: '', tags: [] });
      hook.unmount();
    }
  });

  it('keeps editing when storage is blocked or full', () => {
    const spy = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => {
        throw new Error('quota');
      });
    try {
      const hook = renderHook(() => useDraftState('blocked', ''));
      act(() => hook.result.current[1]('still here'));
      expect(hook.result.current[0]).toBe('still here');
    } finally {
      spy.mockRestore();
    }
  });
});
