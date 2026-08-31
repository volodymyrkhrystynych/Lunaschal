// @vitest-environment jsdom
/**
 * The panel that makes the nightly pass fair to run. What matters here is that
 * the *facts* are reachable and correctable — the article prose is regenerated
 * from them each time, so editing the paragraph would be undone on the next
 * render while fixing a fact sticks.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { LifeWikiSection } from './LifeWikiSection';

vi.mock('../../hooks/api', () => ({
  api: {
    lifeWiki: {
      list: vi.fn(),
      get: vi.fn(),
      update: vi.fn(),
      setLocked: vi.fn(),
      rebuild: vi.fn(),
      lockFact: vi.fn(),
      deleteFact: vi.fn(),
    },
  },
}));

const renderSection = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <LifeWikiSection />
    </QueryClientProvider>
  );

const ARTICLE = {
  id: 'a1',
  slug: 'health-and-training',
  title: 'Health and training',
  summary: 'How they train.',
  content: 'They train three times a week.',
  locked: 0,
  revision: 2,
  updatedAt: '2026-03-04T05:00:00',
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.lifeWiki.list).mockResolvedValue([ARTICLE]);
  vi.mocked(api.lifeWiki.get).mockResolvedValue({
    ...ARTICLE,
    facts: [
      {
        id: 'f1',
        statement: 'Trains three times a week.',
        sourceKind: 'journal',
        sourceId: '01JOURNAL',
        locked: 0,
        firstSeen: '2026-03-01T09:00:00',
        lastSeen: '2026-03-04T09:00:00',
      },
    ],
  });
  vi.mocked(api.lifeWiki.rebuild).mockResolvedValue({ rebuilding: true });
  vi.mocked(api.lifeWiki.deleteFact).mockResolvedValue({ ok: true });
});

describe('LifeWikiSection', () => {
  it('lists the articles with their summaries', async () => {
    renderSection();
    expect(await screen.findByText('Health and training')).toBeTruthy();
    expect(screen.getByText('How they train.')).toBeTruthy();
  });

  it('says so plainly before the first pass has run', async () => {
    vi.mocked(api.lifeWiki.list).mockResolvedValue([]);
    renderSection();
    expect(await screen.findByText(/Nothing written yet/)).toBeTruthy();
  });

  it('shows the facts an article was built from, and where each came from', async () => {
    renderSection();
    fireEvent.click(await screen.findByText('Health and training'));
    expect(await screen.findByText('Trains three times a week.')).toBeTruthy();
    expect(screen.getByText(/from journal/)).toBeTruthy();
  });

  it('deletes a fact', async () => {
    renderSection();
    fireEvent.click(await screen.findByText('Health and training'));
    fireEvent.click(
      await screen.findByLabelText('Delete fact: Trains three times a week.')
    );
    await waitFor(() =>
      expect(api.lifeWiki.deleteFact).toHaveBeenCalledWith('f1')
    );
  });

  it('keeps a fact so the pass can never overrule it', async () => {
    renderSection();
    fireEvent.click(await screen.findByText('Health and training'));
    fireEvent.click(
      await screen.findByLabelText('Keep fact: Trains three times a week.')
    );
    await waitFor(() =>
      expect(api.lifeWiki.lockFact).toHaveBeenCalledWith('f1', true)
    );
  });

  it('starts a rebuild and says it is running rather than pretending it is done', async () => {
    renderSection();
    fireEvent.click(await screen.findByText('Health and training'));
    fireEvent.click(await screen.findByText('Rebuild from source'));
    await waitFor(() =>
      expect(api.lifeWiki.rebuild).toHaveBeenCalledWith('health-and-training')
    );
    expect(await screen.findByText(/reopen in a minute/)).toBeTruthy();
  });
});
