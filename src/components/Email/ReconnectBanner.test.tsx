// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type EmailAccountStatus } from '../../hooks/api';
import { parseEmailNotice } from '../../lib/email';
import { EmailCards } from '../Chat/EmailCards';
import { Email } from '.';

const account: EmailAccountStatus = {
  provider: 'gmail',
  emailAddress: 'me@example.com',
  connected: false,
  needsReconnect: true,
  lastSyncedAt: null,
  lastSyncError: null,
  syncEnabled: true,
};

describe('a lost email connection', () => {
  it('keeps the inbox, offers sign-in, and filters important mail', async () => {
    vi.spyOn(api.email, 'accounts').mockResolvedValue([account]);
    const list = vi.spyOn(api.email, 'list').mockResolvedValue([]);
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Email />
      </QueryClientProvider>
    );
    fireEvent.click(await screen.findByText('Reconnect'));
    expect(open.mock.calls[0][0]).toBe(
      '/api/email/oauth/authorize?provider=gmail'
    );
    fireEvent.click(screen.getByText('Important'));
    await vi.waitFor(() =>
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ importance: 'high' })
      )
    );
  });
});

describe('email chat cards', () => {
  it('lists the emails a message is about', () => {
    const metadata = JSON.stringify({
      email: {
        kind: 'important',
        items: [
          { id: 'a', sender: 'Dana', subject: 'Next', reason: 'deadline' },
        ],
      },
    });
    render(<EmailCards metadata={metadata} />);
    expect(screen.getByText('Dana')).toBeTruthy();
    expect(screen.getByText('Deadline')).toBeTruthy();
  });

  it('ignores everything else', () => {
    expect(parseEmailNotice(null)).toBeNull();
    expect(parseEmailNotice('nope')).toBeNull();
    expect(parseEmailNotice('{"coach":{}}')).toBeNull();
    expect(
      parseEmailNotice('{"email":{"kind":"important","items":7}}')
    ).toBeNull();
  });
});
