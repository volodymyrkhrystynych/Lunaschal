import { describe, it, expect, vi, afterEach } from 'vitest';
import { onlineManager } from '@tanstack/react-query';
import { recheckOnline } from './onlineManager';

afterEach(() => {
  onlineManager.setOnline(true); // don't leak offline state to other tests
  vi.restoreAllMocks();
});

describe('recheckOnline', () => {
  it('reports online when /api/health responds ok', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true }) as Response)
    );
    expect(await recheckOnline()).toBe(true);
    expect(onlineManager.isOnline()).toBe(true);
  });

  it('reports offline when the backend is unreachable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('network down');
      })
    );
    expect(await recheckOnline()).toBe(false);
    expect(onlineManager.isOnline()).toBe(false);
  });

  it('reports offline on a non-ok health response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false }) as Response)
    );
    expect(await recheckOnline()).toBe(false);
  });
});
