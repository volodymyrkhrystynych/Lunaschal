import { describe, expect, it, vi } from 'vitest';
import {
  BASE_URL,
  callTool,
  listTools,
  register,
} from '../config/pi/extensions/lunaschal.js';

function json(body, status = 200) {
  return {
    ok: status < 400,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

const TOOL = {
  name: 'library_search',
  description: 'Search the library',
  parameters: { type: 'object', properties: { queries: { type: 'array' } } },
};

describe('register', () => {
  it('registers exactly what the host lists', async () => {
    const pi = { registerTool: vi.fn() };
    await register(pi, async () => json({ tools: [TOOL] }));
    expect(pi.registerTool).toHaveBeenCalledTimes(1);
    const [tool] = pi.registerTool.mock.calls[0];
    expect(tool.name).toBe('library_search');
    expect(tool.description).toBe('Search the library');
    expect(tool.parameters).toEqual(TOOL.parameters);
  });

  it('registers nothing when the host cannot be reached', async () => {
    const pi = { registerTool: vi.fn() };
    await register(pi, async () => {
      throw new Error('ECONNREFUSED');
    });
    expect(pi.registerTool).not.toHaveBeenCalled();
  });

  it('execute forwards to the host', async () => {
    const pi = { registerTool: vi.fn() };
    const calls = [];
    const fetchImpl = async (url, init) => {
      calls.push([url, init]);
      return init
        ? json({ text: '3 results', ok: true })
        : json({ tools: [TOOL] });
    };
    await register(pi, fetchImpl);
    const [tool] = pi.registerTool.mock.calls[0];
    const out = await tool.execute('t1', { queries: ['flask'] }, undefined);
    expect(out.content).toEqual([{ type: 'text', text: '3 results' }]);
    expect(calls[1][0]).toBe(`${BASE_URL}/library_search`);
    expect(JSON.parse(calls[1][1].body)).toEqual({ queries: ['flask'] });
  });
});

describe('listTools', () => {
  it('reads a bad answer as no tools', async () => {
    expect(await listTools(async () => json({}, 500))).toEqual([]);
    expect(await listTools(async () => json({ tools: 'nope' }))).toEqual([]);
  });
});

describe('callTool', () => {
  it('parses a body led by keepalive whitespace', async () => {
    const fetchImpl = async () => ({
      ok: true,
      status: 200,
      text: async () => '   {"text":"done","ok":true}',
    });
    const out = await callTool(
      'web_research',
      { question: 'q' },
      undefined,
      fetchImpl
    );
    expect(out.content[0].text).toBe('done');
  });

  it('turns a refusal into text', async () => {
    const out = await callTool('code_map', {}, undefined, async () =>
      json({ text: 'No lookup tool named code_map.', ok: false }, 404)
    );
    expect(out.content[0].text).toBe('No lookup tool named code_map.');
  });

  it('turns a network failure into text, not a throw', async () => {
    const out = await callTool('library_read', {}, undefined, async () => {
      throw new Error('socket hang up');
    });
    expect(out.content[0].text).toBe('library_read failed: socket hang up');
  });

  it('says a cancelled call was cancelled', async () => {
    const ctl = new AbortController();
    ctl.abort();
    const out = await callTool('web_research', {}, ctl.signal, async () => {
      throw new Error('aborted');
    });
    expect(out.content[0].text).toBe('web_research was cancelled.');
  });
});
