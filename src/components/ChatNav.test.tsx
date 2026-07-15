// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../hooks/api';
import { ChatNav } from './ChatNav';

vi.mock('../hooks/api', () => ({
  api: {
    chat: {
      listConversations: vi.fn().mockResolvedValue([
        { id: 'c1', title: 'Morning plans', createdAt: '', updatedAt: '' },
        { id: 'c2', title: 'Recipe ideas', createdAt: '', updatedAt: '' },
      ]),
      deleteConversation: vi.fn().mockResolvedValue({ ok: true }),
    },
  },
}));

beforeEach(() => {
  vi.mocked(api.chat.deleteConversation).mockClear();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});

function renderWithProviders(children: ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{children}</QueryClientProvider>);
}

describe('ChatNav', () => {
  it('renders conversation titles and highlights the selected one', async () => {
    renderWithProviders(<ChatNav currentConversationId="c2" onSelect={() => {}} />);

    const selected = await screen.findByText('Recipe ideas');
    expect(screen.getByText('Morning plans')).not.toBeNull();
    expect(selected.parentElement!.className).toContain('bg-[var(--color-primary)]/20');
    expect(screen.getByText('Morning plans').parentElement!.className).not.toContain('bg-[var(--color-primary)]/20');
  });

  it('selects a conversation on click and starts a new one via +', async () => {
    const onSelect = vi.fn();
    renderWithProviders(<ChatNav currentConversationId={null} onSelect={onSelect} />);

    fireEvent.click(await screen.findByText('Morning plans'));
    expect(onSelect).toHaveBeenCalledWith('c1');

    fireEvent.click(screen.getByTitle('New conversation'));
    expect(onSelect).toHaveBeenCalledWith(null);
  });

  it('deletes a conversation and clears selection when it was current', async () => {
    const onSelect = vi.fn();
    renderWithProviders(<ChatNav currentConversationId="c1" onSelect={onSelect} />);

    await screen.findByText('Morning plans');
    fireEvent.click(screen.getAllByTitle('Delete')[0]);

    await vi.waitFor(() => expect(vi.mocked(api.chat.deleteConversation).mock.calls[0][0]).toBe('c1'));
    await vi.waitFor(() => expect(onSelect).toHaveBeenCalledWith(null));
  });

  it('shows an empty state when there are no conversations', async () => {
    vi.mocked(api.chat.listConversations).mockResolvedValueOnce([]);
    renderWithProviders(<ChatNav currentConversationId={null} onSelect={() => {}} />);

    expect(await screen.findByText('No conversations yet')).not.toBeNull();
  });
});
