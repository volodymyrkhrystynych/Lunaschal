// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import { AddTorrent } from './AddTorrent';

vi.mock('@/hooks/api', () => ({
  api: {
    settings: { get: vi.fn().mockResolvedValue({}) },
    torrents: {
      add: vi.fn(),
      addFiles: vi.fn().mockResolvedValue({ added: [], errors: [] }),
    },
  },
}));

beforeEach(() => vi.clearAllMocks());

function renderForm() {
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AddTorrent categories={[]} onClose={onClose} />
    </QueryClientProvider>
  );
  return onClose;
}

it('allows files with unknown MIME types and enables a file-only upload immediately', async () => {
  const onClose = renderForm();
  const input = screen.getByLabelText('Torrent files (.torrent)');
  const add = screen.getByRole('button', { name: 'Add' }) as HTMLButtonElement;
  // Native iPad pickers can disable .torrent files when accept is present.
  expect(input.hasAttribute('accept')).toBe(false);
  expect(add.disabled).toBe(true);
  const files = [
    new File(['torrent one'], 'one.torrent'),
    new File(['torrent two'], 'two.torrent', {
      type: 'application/octet-stream',
    }),
  ];
  fireEvent.change(input, { target: { files } });
  expect(add.disabled).toBe(false);
  fireEvent.click(add);
  await waitFor(() =>
    expect(api.torrents.addFiles).toHaveBeenCalledWith(files, {
      category: undefined,
      note: undefined,
      retentionDays: 0,
    })
  );
  expect(api.torrents.add).not.toHaveBeenCalled();
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
});

it('disables Add when the selected files are cleared', () => {
  renderForm();
  const input = screen.getByLabelText('Torrent files (.torrent)');
  const add = screen.getByRole('button', { name: 'Add' }) as HTMLButtonElement;
  fireEvent.change(input, {
    target: { files: [new File(['data'], 'one.torrent')] },
  });
  expect(add.disabled).toBe(false);
  fireEvent.change(input, { target: { files: [] } });
  expect(add.disabled).toBe(true);
});

it('keeps the form open and displays rejected file errors', async () => {
  vi.mocked(api.torrents.addFiles).mockResolvedValueOnce({
    added: [],
    errors: [{ input: 'wrong.txt', error: 'Not a torrent file.' }],
  });
  const onClose = renderForm();
  fireEvent.change(screen.getByLabelText('Torrent files (.torrent)'), {
    target: { files: [new File(['text'], 'wrong.txt')] },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Add' }));
  expect(await screen.findByText(/Not a torrent file/)).toBeTruthy();
  expect(onClose).not.toHaveBeenCalled();
});
