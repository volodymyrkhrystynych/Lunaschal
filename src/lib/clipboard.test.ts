// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { copyText } from './clipboard';

afterEach(() => {
  vi.restoreAllMocks();
  delete (document as Document & { execCommand?: Document['execCommand'] })
    .execCommand;
});

function stubExecCommand(result: boolean) {
  const execCommand = vi.fn().mockReturnValue(result);
  Object.defineProperty(document, 'execCommand', {
    configurable: true,
    value: execCommand,
  });
  return execCommand;
}

describe('copyText', () => {
  it('uses the async Clipboard API when it is available', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    const execCommand = stubExecCommand(false);

    await copyText('log line');

    expect(writeText).toHaveBeenCalledWith('log line');
    expect(execCommand).not.toHaveBeenCalled();
  });

  it('falls back to the selected hidden textarea when the API is blocked', async () => {
    vi.stubGlobal('navigator', {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    });
    const execCommand = stubExecCommand(true);

    await copyText('log line');

    expect(execCommand).toHaveBeenCalledWith('copy');
    expect(document.querySelector('textarea')).toBeNull();
  });

  it('reports failure when neither copy method succeeds', async () => {
    vi.stubGlobal('navigator', { clipboard: undefined });
    stubExecCommand(false);

    await expect(copyText('log line')).rejects.toThrow(
      'Clipboard is unavailable'
    );
  });
});
