import { describe, it, expect } from 'vitest';
import { isBreak, splitSegments, contextMessages } from './chatSegments';
import type { Message } from '../hooks/api';

let seq = 0;
function msg(
  role: Message['role'],
  content: string,
  metadata: string | null = null
): Message {
  return {
    id: `m${seq++}`,
    conversationId: 'c1',
    role,
    content,
    metadata,
    createdAt: '2026-07-25T12:00:00',
  };
}

const brk = () => msg('system', '', JSON.stringify({ break: true }));

describe('isBreak', () => {
  it('is true only for system messages flagged break', () => {
    expect(isBreak(brk())).toBe(true);
    expect(isBreak(msg('system', '', '{"break":false}'))).toBe(false);
    expect(isBreak(msg('system', '', null))).toBe(false);
    expect(isBreak(msg('user', '', '{"break":true}'))).toBe(false);
    expect(isBreak(msg('system', '', 'not json'))).toBe(false);
  });
});

describe('splitSegments', () => {
  it('returns a single segment when there are no breaks', () => {
    const messages = [msg('user', 'hi'), msg('assistant', 'hello')];
    const segments = splitSegments(messages);
    expect(segments).toHaveLength(1);
    expect(segments[0].map(m => m.content)).toEqual(['hi', 'hello']);
  });

  it('splits at each break and drops the markers', () => {
    const messages = [
      msg('user', 'a'),
      msg('assistant', 'A'),
      brk(),
      msg('user', 'b'),
      brk(),
      msg('user', 'c'),
    ];
    const segments = splitSegments(messages);
    expect(segments.map(s => s.map(m => m.content))).toEqual([
      ['a', 'A'],
      ['b'],
      ['c'],
    ]);
  });

  it('yields an empty trailing segment when a break is last', () => {
    const messages = [msg('user', 'a'), brk()];
    const segments = splitSegments(messages);
    expect(segments).toHaveLength(2);
    expect(segments[1]).toEqual([]);
  });

  it('always returns at least one segment for empty input', () => {
    expect(splitSegments([])).toEqual([[]]);
  });
});

describe('contextMessages', () => {
  it('returns everything when there is no break', () => {
    const messages = [msg('user', 'a'), msg('assistant', 'A')];
    expect(contextMessages(messages).map(m => m.content)).toEqual(['a', 'A']);
  });

  it('returns only messages after the last break', () => {
    const messages = [
      msg('user', 'a'),
      brk(),
      msg('user', 'b'),
      msg('assistant', 'B'),
    ];
    expect(contextMessages(messages).map(m => m.content)).toEqual(['b', 'B']);
  });

  it('is empty right after a fresh break', () => {
    const messages = [msg('user', 'a'), brk()];
    expect(contextMessages(messages)).toEqual([]);
  });
});
