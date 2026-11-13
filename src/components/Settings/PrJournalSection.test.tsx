// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PrJournalSection } from './PrJournalSection';
import { api, type AppSettings } from '../../hooks/api';

vi.mock('../../hooks/api', () => ({
  api: { settings: { get: vi.fn(), updateAI: vi.fn() } },
}));

function renderWith(over: Partial<AppSettings>) {
  vi.mocked(api.settings.get).mockResolvedValue({
    prJournalRepos: [],
    hasGithubToken: false,
    ...over,
  } as AppSettings);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <PrJournalSection />
    </QueryClientProvider>
  );
}

describe('PrJournalSection', () => {
  beforeEach(() => {
    vi.mocked(api.settings.updateAI).mockReset();
    vi.mocked(api.settings.updateAI).mockResolvedValue({ success: true });
  });

  it('adds a repository to the existing list', async () => {
    renderWith({ prJournalRepos: ['o/r'] });
    await screen.findByText('o/r');
    fireEvent.change(screen.getByLabelText('Repository'), {
      target: { value: ' https://github.com/a/b ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() =>
      expect(api.settings.updateAI).toHaveBeenCalledWith({
        prJournalRepos: ['o/r', 'https://github.com/a/b'],
      })
    );
  });

  it('removes one repository and keeps the rest', async () => {
    renderWith({ prJournalRepos: ['o/r', 'a/b'] });
    fireEvent.click(await screen.findByLabelText('Remove o/r'));
    await waitFor(() =>
      expect(api.settings.updateAI).toHaveBeenCalledWith({
        prJournalRepos: ['a/b'],
      })
    );
  });

  it("shows the server's reason when a repository is refused", async () => {
    vi.mocked(api.settings.updateAI).mockRejectedValue(
      new Error("Not a GitHub repository: 'nope' (expected owner/name)")
    );
    renderWith({});
    fireEvent.change(await screen.findByLabelText('Repository'), {
      target: { value: 'nope' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect((await screen.findByRole('alert')).textContent).toContain(
      'Not a GitHub repository'
    );
  });

  it('says whether a private repository can be read', async () => {
    renderWith({ hasGithubToken: false });
    expect(
      await screen.findByText(/only public repositories will work/)
    ).toBeTruthy();
  });
});
