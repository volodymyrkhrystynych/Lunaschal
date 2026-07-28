import { describe, it, expect } from 'vitest';
import { readSSE } from './sse';

/** Fakes a fetch() response body reader over a fixed sequence of chunks —
 * each chunk arriving as its own `reader.read()` result, exactly like the
 * real stream delivers one network packet at a time. */
function fakeReader(
  chunks: Uint8Array[]
): ReadableStreamDefaultReader<Uint8Array> {
  let i = 0;
  return {
    read: async () => {
      if (i < chunks.length) return { done: false, value: chunks[i++] };
      return { done: true, value: undefined };
    },
  } as ReadableStreamDefaultReader<Uint8Array>;
}

const enc = (s: string) => new TextEncoder().encode(s);

async function collect(reader: ReadableStreamDefaultReader<Uint8Array>) {
  const events = [];
  for await (const e of readSSE(reader)) events.push(e);
  return events;
}

describe('readSSE', () => {
  it('parses a single complete event in one chunk', async () => {
    const events = await collect(
      fakeReader([enc('data: {"content":"hi"}\n\n')])
    );
    expect(events).toEqual([{ content: 'hi' }]);
  });

  it('parses multiple events delivered in one chunk', async () => {
    const events = await collect(
      fakeReader([enc('data: {"content":"a"}\n\ndata: {"content":"b"}\n\n')])
    );
    expect(events).toEqual([{ content: 'a' }, { content: 'b' }]);
  });

  it('reassembles a data line split across two chunks', async () => {
    const whole = 'data: {"content":"hello world"}\n\n';
    const splitAt = whole.indexOf('"hello');
    const events = await collect(
      fakeReader([enc(whole.slice(0, splitAt)), enc(whole.slice(splitAt))])
    );
    expect(events).toEqual([{ content: 'hello world' }]);
  });

  it('reassembles a multi-byte UTF-8 character split across chunk boundaries', async () => {
    // "日" is 3 bytes in UTF-8 (E6 97 A5) — split the encoded payload mid-character.
    const bytes = enc('data: {"content":"日"}\n\n');
    const splitPoint = bytes.indexOf(0xe6) + 2; // inside the 3-byte sequence
    const events = await collect(
      fakeReader([bytes.slice(0, splitPoint), bytes.slice(splitPoint)])
    );
    expect(events).toEqual([{ content: '日' }]);
  });

  it('stops at the [DONE] sentinel without yielding it', async () => {
    const events = await collect(
      fakeReader([
        enc(
          'data: {"content":"a"}\n\ndata: [DONE]\n\ndata: {"content":"never"}\n\n'
        ),
      ])
    );
    expect(events).toEqual([{ content: 'a' }]);
  });

  it('skips malformed JSON without throwing', async () => {
    const events = await collect(
      fakeReader([enc('data: not json\n\ndata: {"content":"ok"}\n\n')])
    );
    expect(events).toEqual([{ content: 'ok' }]);
  });

  it('ignores non-data lines', async () => {
    const events = await collect(
      fakeReader([enc(': keep-alive comment\n\ndata: {"content":"a"}\n\n')])
    );
    expect(events).toEqual([{ content: 'a' }]);
  });

  it('yields an error payload instead of swallowing it, leaving the throw decision to the caller', async () => {
    const events = await collect(
      fakeReader([enc('data: {"error":"boom"}\n\n')])
    );
    expect(events).toEqual([{ error: 'boom' }]);
  });

  it('yields nothing for an empty stream', async () => {
    const events = await collect(fakeReader([]));
    expect(events).toEqual([]);
  });
});
