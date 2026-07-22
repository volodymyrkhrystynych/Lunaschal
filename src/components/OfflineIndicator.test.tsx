// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import {
  QueryClient,
  QueryClientProvider,
  onlineManager,
} from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { OfflineIndicator } from './OfflineIndicator';

const wrapper = (qc: QueryClient) =>
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };

afterEach(() => onlineManager.setOnline(true));

describe('OfflineIndicator', () => {
  it('renders nothing when online with no pending writes', () => {
    onlineManager.setOnline(true);
    const qc = new QueryClient();
    const { container } = render(<OfflineIndicator />, {
      wrapper: wrapper(qc),
    });
    expect(container.firstChild).toBeNull();
  });

  it('shows an offline banner with a retry action when the backend is unreachable', () => {
    onlineManager.setOnline(false);
    const qc = new QueryClient();
    render(<OfflineIndicator />, { wrapper: wrapper(qc) });
    expect(screen.getByRole('status').textContent).toMatch(/offline/i);
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy();
  });
});
