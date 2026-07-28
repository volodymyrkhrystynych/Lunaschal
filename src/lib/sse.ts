/** A parsed `data: {...}` payload from the chat SSE stream. */
export interface SSEEvent {
  content?: string;
  error?: string;
  [key: string]: unknown;
}

/**
 * Reads Server-Sent Events off a `fetch()` response body reader, yielding
 * each parsed `data: {...}` payload until a `data: [DONE]` line or the
 * stream ends.
 *
 * `TextDecoder.decode` is called with `{ stream: true }` and any trailing
 * partial line is carried over to the next chunk, so a multi-byte UTF-8
 * character or a `data:` line split across two `reader.read()` calls is
 * never dropped (the previous per-chunk `decoder.decode(value).split('\n')`
 * approach silently lost content on either kind of split). Malformed JSON is
 * skipped; a `{error}` payload is yielded like any other event — it's the
 * caller's job to decide whether to throw, so that throw isn't swallowed by
 * this function's own JSON-parse error handling.
 */
export async function* readSSE(
  reader: ReadableStreamDefaultReader<Uint8Array>
): AsyncGenerator<SSEEvent> {
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      const data = line.slice(6);
      if (data === '[DONE]') return;
      try {
        yield JSON.parse(data) as SSEEvent;
      } catch {
        /* ignore parse errors */
      }
    }
  }
}
