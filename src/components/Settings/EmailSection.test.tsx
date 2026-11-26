// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type EmailAccountStatus, type EmailProvider } from '@/hooks/api';
import { EmailSection } from './EmailSection';

describe('a connection that needs signing in again', () => {
  it.each<EmailProvider>(['gmail', 'outlook', 'imap'])(
    'can still be disconnected: %s',
    async provider => {
      const account: EmailAccountStatus = {
        provider,
        emailAddress: 'me@example.com',
        connected: false,
        needsReconnect: true,
        lastSyncedAt: null,
        lastSyncError: null,
        syncEnabled: true,
      };
      vi.spyOn(api.settings, 'get').mockResolvedValue(
        {} as Awaited<ReturnType<typeof api.settings.get>>
      );
      vi.spyOn(api.email, 'accounts').mockResolvedValue([account]);
      const disconnect = vi
        .spyOn(api.email, 'disconnect')
        .mockResolvedValue({ success: true });
      render(
        <QueryClientProvider client={new QueryClient()}>
          <EmailSection />
        </QueryClientProvider>
      );
      fireEvent.click(await screen.findByText('Disconnect'));
      await vi.waitFor(() => expect(disconnect).toHaveBeenCalledWith(provider));
    }
  );
});
