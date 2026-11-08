/**
 * Lunaschal lookups for pi: the offline library, this repository's wiki and
 * code graph, and (when enabled) web research.
 *
 * The container has no network. These tools call the host through the same
 * socket the model calls use (socat's end at 127.0.0.1:8080), and the host
 * runs the lookup — backend/codeagent/lookups.py. The host decides which
 * tools exist and writes their descriptions, so this file only forwards.
 *
 * No imports, on purpose: tool parameters are plain JSON Schema (what TypeBox
 * produces at runtime anyway), so Vitest can load this file as it is.
 */

export const BASE_URL = 'http://127.0.0.1:8080/lunaschal/tools';

function text(value) {
  return { content: [{ type: 'text', text: value }], details: {} };
}

/** Asks the host what this task may look up. [] when it cannot be reached. */
export async function listTools(fetchImpl = fetch) {
  try {
    const res = await fetchImpl(BASE_URL);
    if (!res.ok) return [];
    const body = await res.json();
    return Array.isArray(body?.tools) ? body.tools : [];
  } catch {
    return [];
  }
}

/** One lookup. Failures come back as text the model reads, never a throw. */
export async function callTool(name, params, signal, fetchImpl = fetch) {
  try {
    const res = await fetchImpl(`${BASE_URL}/${encodeURIComponent(name)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params ?? {}),
      signal,
    });
    // The host sends whitespace keepalives before the JSON while a slow
    // lookup runs; JSON.parse ignores leading whitespace.
    const raw = await res.text();
    let body = null;
    try {
      body = JSON.parse(raw);
    } catch {
      body = null;
    }
    if (body && typeof body.text === 'string') return text(body.text);
    return text(`${name} failed: the host answered ${res.status}.`);
  } catch (e) {
    if (signal?.aborted) return text(`${name} was cancelled.`);
    return text(`${name} failed: ${e?.message ?? e}`);
  }
}

// The factory pi calls takes the API alone: any second argument pi passes is
// not ours to read as a fetch.
export default async function lunaschal(pi) {
  await register(pi, fetch);
}

export async function register(pi, fetchImpl) {
  const tools = await listTools(fetchImpl);
  for (const tool of tools) {
    pi.registerTool({
      name: tool.name,
      label: tool.name,
      description: tool.description,
      parameters: tool.parameters,
      async execute(_toolCallId, params, signal) {
        return callTool(tool.name, params, signal, fetchImpl);
      },
    });
  }
}
