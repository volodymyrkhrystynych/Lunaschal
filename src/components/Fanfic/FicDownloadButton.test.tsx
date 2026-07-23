// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { api, type AuthStatus, type FicChapterSummary } from '../../hooks/api';
import { FicDownloadButton } from './FicDownloadButton';

function chapters(n: number): FicChapterSummary[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `c${i}`,
    ficId: 'f1',
    position: i,
    title: `Chapter ${i}`,
    category: '',
    wordCount: 100,
    postedAt: null,
    isRead: false,
  }));
}

function renderButton(networkMode: boolean) {
  vi.spyOn(api.auth, 'status').mockResolvedValue({
    authenticated: true,
    networkMode,
  } as AuthStatus);
  const qc = new QueryClient();
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return render(<FicDownloadButton chapters={chapters(3)} />, {
    wrapper: Wrapper,
  });
}

afterEach(() => vi.restoreAllMocks());

describe('FicDownloadButton visibility', () => {
  it('shows the offline-save button for a network (thin-client) session', async () => {
    renderButton(true);
    await waitFor(() =>
      expect(screen.getByText(/Save for offline/)).toBeTruthy()
    );
  });

  it('stays hidden on the server / localhost (networkMode false)', async () => {
    renderButton(false);
    // Give the auth query time to resolve; the button must never appear.
    await Promise.resolve();
    await waitFor(() => expect(api.auth.status).toHaveBeenCalled());
    expect(screen.queryByText(/Save for offline/)).toBeNull();
  });
});
