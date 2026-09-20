// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type KnowledgeArchive } from '@/hooks/api';
import { Knowledge } from './Knowledge';

vi.mock('@/hooks/api', () => ({
  api: {
    knowledge: {
      config: vi.fn(),
      archives: vi.fn(),
      search: vi.fn(),
      rescan: vi.fn(),
      updateArchive: vi.fn(),
      contentUrl: (id: string, path: string) =>
        `/api/knowledge/archives/${id}/content/${path}`,
    },
  },
}));

const knowledge = api.knowledge as unknown as {
  config: ReturnType<typeof vi.fn>;
  archives: ReturnType<typeof vi.fn>;
  search: ReturnType<typeof vi.fn>;
  rescan: ReturnType<typeof vi.fn>;
  updateArchive: ReturnType<typeof vi.fn>;
};

function archive(over: Partial<KnowledgeArchive> = {}): KnowledgeArchive {
  return {
    id: 'wiki',
    filename: 'wikipedia_en_all.zim',
    title: 'Wikipedia',
    size: 3_300_000_000,
    date: '2026-06',
    kind: 'encyclopedia',
    enabled: true,
    health: 'ok',
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  knowledge.config.mockResolvedValue({ path: '/zims', exists: true });
  knowledge.archives.mockResolvedValue([archive()]);
  knowledge.search.mockResolvedValue({
    results: [],
    searched: 1,
    skipped: 0,
    tookMs: 4,
  });
});

function renderKnowledge() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Knowledge />
    </QueryClientProvider>
  );
}

it('tells the user where to configure the library when none is set', async () => {
  knowledge.config.mockResolvedValue({ path: '', exists: false });
  renderKnowledge();
  expect(
    await screen.findByText('No knowledge library configured')
  ).toBeTruthy();
});

it('groups archives by kind and labels a title-only archive without calling it broken', async () => {
  knowledge.archives.mockResolvedValue([
    archive(),
    archive({
      id: 'lit',
      title: 'Lit',
      kind: 'docs',
      health: 'no_fulltext',
      filename: 'devdocs_en_lit.zim',
    }),
  ]);
  renderKnowledge();

  expect(await screen.findByText('Encyclopedias')).toBeTruthy();
  expect(screen.getByText('Documentation')).toBeTruthy();
  const badge = screen.getByText('title search only');
  expect(badge.className).toContain('amber');
});

it('turning an archive off patches it and refetches the library', async () => {
  knowledge.updateArchive.mockResolvedValue(archive({ enabled: false }));
  renderKnowledge();

  const toggle = await screen.findByLabelText('Search Wikipedia');
  fireEvent.click(toggle);

  await waitFor(() =>
    expect(knowledge.updateArchive).toHaveBeenCalledWith('wiki', {
      enabled: false,
    })
  );
});

it('shows which archive each result came from', async () => {
  knowledge.search.mockResolvedValue({
    results: [
      {
        archiveId: 'so',
        archiveTitle: 'Stack Overflow',
        archiveDate: '2026-07',
        archiveKind: 'qa',
        path: 'Thread',
        title: 'Why does this segfault',
        snippet: '',
        matchKind: 'fulltext',
      },
    ],
    searched: 2,
    skipped: 0,
    tookMs: 12,
  });
  renderKnowledge();

  fireEvent.change(await screen.findByLabelText('Search offline library'), {
    target: { value: 'segfault' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));

  expect(await screen.findByText('Why does this segfault')).toBeTruthy();
  // Without the source, a Q&A thread and an encyclopedia article are
  // indistinguishable in a merged list.
  expect(screen.getByText('Q&A')).toBeTruthy();
  expect(screen.getByText(/Stack Overflow/)).toBeTruthy();
});

it('says so when archives were left unsearched', async () => {
  knowledge.search.mockResolvedValue({
    results: [],
    searched: 3,
    skipped: 9,
    tookMs: 4000,
  });
  renderKnowledge();

  fireEvent.change(await screen.findByLabelText('Search offline library'), {
    target: { value: 'anything' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));

  expect(await screen.findByText(/9 archives skipped/)).toBeTruthy();
});

it('opens the chosen article in the sandboxed reader', async () => {
  knowledge.search.mockResolvedValue({
    results: [
      {
        archiveId: 'wiki',
        archiveTitle: 'Wikipedia',
        archiveDate: '2026-06',
        archiveKind: 'encyclopedia',
        path: 'Moon',
        title: 'Moon',
        snippet: '',
        matchKind: 'fulltext',
      },
    ],
    searched: 1,
    skipped: 0,
    tookMs: 6,
  });
  renderKnowledge();

  fireEvent.change(await screen.findByLabelText('Search offline library'), {
    target: { value: 'moon' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  fireEvent.click(await screen.findByText('Moon'));

  const frame = (await screen.findByTitle('Moon')) as HTMLIFrameElement;
  expect(frame.getAttribute('src')).toBe(
    '/api/knowledge/archives/wiki/content/Moon'
  );
  // Archived pages are untrusted documents.
  expect(frame.getAttribute('sandbox')).toBe('');
});
