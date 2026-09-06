// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import {
  installBrowserDiagnostics,
  readBrowserDiagnostics,
  recordBrowserSignal,
} from './browserDiagnostics';

beforeEach(() => sessionStorage.clear());

it('retains earlier boots and captures lifecycle signals without error contents', () => {
  const stop = installBrowserDiagnostics();
  window.dispatchEvent(new Event('pagehide'));
  document.dispatchEvent(new Event('visibilitychange'));
  window.dispatchEvent(
    new ErrorEvent('error', { message: 'private journal words' })
  );
  stop();
  const stopAgain = installBrowserDiagnostics();
  expect(
    readBrowserDiagnostics().filter(e => e.signal === 'boot')
  ).toHaveLength(2);
  expect(readBrowserDiagnostics().map(e => e.signal)).toContain('pagehide');
  expect(JSON.stringify(readBrowserDiagnostics())).not.toContain(
    'private journal words'
  );
  stopAgain();
});

it('bounds history and tolerates unavailable storage', () => {
  for (let i = 0; i < 100; i++) recordBrowserSignal('shell-mount');
  expect(readBrowserDiagnostics()).toHaveLength(80);
  const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  try {
    expect(() => recordBrowserSignal('boot')).not.toThrow();
  } finally {
    spy.mockRestore();
  }
});
