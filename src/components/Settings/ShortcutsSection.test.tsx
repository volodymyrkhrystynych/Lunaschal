// @vitest-environment jsdom
import { vi, it, expect } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { ShortcutsSection } from './ShortcutsSection';

vi.mock('../../hooks/api', () => ({
  api: {
    settings: {
      get: vi.fn().mockResolvedValue({ sttScreenshotKey: 'KEY_F8' }),
      updateShortcuts: vi.fn().mockResolvedValue({ success: true }),
    },
  },
}));

it('records and saves a screenshot binding, and can explicitly disable it', async () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ShortcutsSection />
    </QueryClientProvider>
  );
  fireEvent.click(await screen.findByRole('button', { name: 'F8' }));
  fireEvent.keyDown(window, { code: 'F9' });
  fireEvent.click(screen.getByRole('button', { name: 'Save shortcuts' }));
  await waitFor(() =>
    expect(api.settings.updateShortcuts).toHaveBeenCalledWith(
      expect.objectContaining({ sttScreenshotKey: 'KEY_F9' })
    )
  );
  await waitFor(() =>
    expect(
      (
        screen.getByRole('button', {
          name: 'Save shortcuts',
        }) as HTMLButtonElement
      ).disabled
    ).toBe(false)
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Disable screenshot shortcut' })
  );
  fireEvent.click(screen.getByRole('button', { name: 'Save shortcuts' }));
  await waitFor(() =>
    expect(api.settings.updateShortcuts).toHaveBeenLastCalledWith(
      expect.objectContaining({ sttScreenshotKey: '' })
    )
  );
});
