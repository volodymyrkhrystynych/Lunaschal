// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  api,
  type KnowledgeCatalogEntry,
  type KnowledgeDownload,
} from '@/hooks/api';
import { CatalogPanel } from './CatalogPanel';
import { DownloadStrip } from './DownloadStrip';

vi.mock('@/hooks/api', () => ({
  api: {
    knowledge: {
      config: vi.fn(),
      archives: vi.fn(),
      catalog: vi.fn(),
      facets: vi.fn(),
      downloads: vi.fn(),
      download: vi.fn(),
      pauseDownload: vi.fn(),
      resumeDownload: vi.fn(),
      deleteDownload: vi.fn(),
    },
  },
}));

const knowledge = api.knowledge as unknown as Record<
  string,
  ReturnType<typeof vi.fn>
>;

function entry(
  over: Partial<KnowledgeCatalogEntry> = {}
): KnowledgeCatalogEntry {
  return {
    uuid: 'u1',
    name: 'devdocs_en_sinon',
    title: 'Sinon.JS Docs',
    summary: 'Sinon.JS documentation, by DevDocs',
    language: 'eng',
    flavour: '',
    category: '',
    creator: 'DevDocs',
    tags: 'devdocs;_ftindex:no',
    kind: 'docs',
    ftindex: false,
    articleCount: 15,
    mediaCount: 0,
    issued: '2026-08-02',
    meta4Url: 'https://download.example.org/a.zim.meta4',
    approxSize: 361472,
    ...over,
  };
}

function download(over: Partial<KnowledgeDownload> = {}): KnowledgeDownload {
  return {
    id: 'd1',
    name: 'devdocs_en_sinon',
    filename: 'devdocs_en_sinon_2026-08.zim',
    title: 'Sinon.JS Docs',
    status: 'downloading',
    error: null,
    totalBytes: 1000,
    downloadedBytes: 250,
    bytesPerSecond: null,
    sourceUrl: '',
    createdAt: 0,
    finishedAt: null,
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  knowledge.config.mockResolvedValue({
    path: '/zims',
    exists: true,
    writeState: 'writable',
    writeReason: null,
  });
  knowledge.archives.mockResolvedValue([]);
  knowledge.downloads.mockResolvedValue([]);
  knowledge.facets.mockResolvedValue({
    categories: [],
    languages: [{ label: 'English', code: 'eng', count: 1301 }],
  });
  knowledge.catalog.mockResolvedValue({
    entries: [entry()],
    total: 231,
    start: 0,
  });
});

function renderWith(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
  return client;
}

it("reports the catalogue's fulltext claim as a claim, before downloading", async () => {
  renderWith(<CatalogPanel onClose={() => {}} />);
  // Shown before the download rather than after — but attributed to the
  // catalogue, because the catalogue's flag disagrees with the archives it
  // describes.
  expect(await screen.findByText(/title search only/i)).toBeTruthy();
});

it('shows how much of the catalogue the filter actually matched', async () => {
  renderWith(<CatalogPanel onClose={() => {}} />);
  // "1 of 231", not "1 result" — a page count presented as a match count
  // would misdescribe the library.
  expect(await screen.findByText('1–1 of 231')).toBeTruthy();
});

it('sends only the slug and uuid when queueing, never the mirror', async () => {
  knowledge.download.mockResolvedValue(download({ status: 'queued' }));
  renderWith(<CatalogPanel onClose={() => {}} />);
  fireEvent.click(await screen.findByLabelText('Download Sinon.JS Docs'));
  await waitFor(() =>
    expect(knowledge.download).toHaveBeenCalledWith('devdocs_en_sinon', 'u1')
  );
});

it('opens on DevDocs and drops the tag when a title search is run', async () => {
  renderWith(<CatalogPanel onClose={() => {}} />);
  await waitFor(() =>
    expect(knowledge.catalog).toHaveBeenCalledWith({ tag: 'devdocs' })
  );
  fireEvent.change(await screen.findByLabelText('Search the Kiwix catalogue'), {
    target: { value: 'Stack Overflow' },
  });
  fireEvent.click(screen.getByText('Search'));
  // ANDing `q` with `tag=devdocs` would silently hide everything the user
  // just asked for.
  await waitFor(() =>
    expect(knowledge.catalog).toHaveBeenCalledWith({
      q: 'Stack Overflow',
      lang: undefined,
    })
  );
});

it('will not offer to download an archive that is already installed', async () => {
  knowledge.archives.mockResolvedValue([
    {
      id: 'a',
      filename: 'devdocs_en_sinon_2026-08.zim',
      title: 'Sinon.JS Docs',
      size: 1,
      kind: 'docs',
      enabled: true,
      health: 'no_fulltext',
    },
  ]);
  renderWith(<CatalogPanel onClose={() => {}} />);
  const button = await screen.findByLabelText('Download Sinon.JS Docs');
  expect(button.textContent).toBe('Installed');
  expect((button as HTMLButtonElement).disabled).toBe(true);
});

it('says why downloading is blocked when the archive folder is read-only', async () => {
  knowledge.config.mockResolvedValue({
    path: '/zims',
    exists: true,
    writeState: 'readonly',
    writeReason:
      '/zims is on a read-only filesystem, so downloads cannot be saved there.',
  });
  renderWith(<CatalogPanel onClose={() => {}} />);
  expect(await screen.findByRole('alert')).toBeTruthy();
});

it('keeps a failed download in the strip with a way to resume it', async () => {
  knowledge.downloads.mockResolvedValue([
    download({ status: 'error', error: 'Every mirror failed.' }),
  ]);
  renderWith(<DownloadStrip />);
  // The `.part` is kept on failure precisely so the bytes are not lost;
  // offering only Remove would throw them away.
  expect(await screen.findByText('Resume')).toBeTruthy();
  expect(screen.getByText('Every mirror failed.')).toBeTruthy();
});

it('will not remove a download while its thread is still writing', async () => {
  knowledge.downloads.mockResolvedValue([download({ status: 'downloading' })]);
  renderWith(<DownloadStrip />);
  expect(await screen.findByText('Pause')).toBeTruthy();
  expect(screen.queryByText('Remove')).toBeNull();
});

it('hides itself entirely once nothing is in flight', async () => {
  knowledge.downloads.mockResolvedValue([download({ status: 'done' })]);
  const { container } = render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <DownloadStrip />
    </QueryClientProvider>
  );
  await waitFor(() => expect(knowledge.downloads).toHaveBeenCalled());
  // A finished archive belongs in the library list above, not in a strip of
  // things still happening.
  expect(container.textContent).toBe('');
});

it('refreshes installed archives when a polled download finishes', async () => {
  knowledge.downloads.mockResolvedValue([download()]);
  const client = renderWith(
    <>
      <CatalogPanel onClose={() => {}} />
      <DownloadStrip />
    </>
  );
  expect(await screen.findByText('Pause')).toBeTruthy();
  await waitFor(() => expect(knowledge.archives).toHaveBeenCalledTimes(1));
  knowledge.archives.mockResolvedValue([{ filename: download().filename }]);
  knowledge.downloads.mockResolvedValue([download({ status: 'done' })]);
  await client.refetchQueries({ queryKey: ['knowledge', 'downloads'] });
  expect(await screen.findByText('Installed')).toBeTruthy();
  expect(screen.queryByText('Pause')).toBeNull();
  expect(knowledge.archives).toHaveBeenCalledTimes(2);
  await client.refetchQueries({ queryKey: ['knowledge', 'downloads'] });
  expect(knowledge.archives).toHaveBeenCalledTimes(2);
});

it('browses beyond the first page and returns to the previous page', async () => {
  knowledge.catalog.mockImplementation(async filters => {
    const start = Number(filters.start ?? 0);
    return {
      start,
      total: 25,
      entries: Array.from({ length: start === 0 ? 24 : 1 }, (_, i) =>
        entry({
          uuid: `u${start + i}`,
          title: `Archive ${start + i}`,
        })
      ),
    };
  });
  renderWith(<CatalogPanel onClose={() => {}} />);
  expect(await screen.findByText('1–24 of 25')).toBeTruthy();
  expect((screen.getByText('Previous') as HTMLButtonElement).disabled).toBe(
    true
  );
  fireEvent.click(screen.getByText('Next'));
  expect(await screen.findByText('25–25 of 25')).toBeTruthy();
  expect(screen.getByText('Archive 24')).toBeTruthy();
  expect((screen.getByText('Next') as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByText('Previous'));
  expect(await screen.findByText('1–24 of 25')).toBeTruthy();
});

it.each(['language', 'search', 'shortcut'])(
  'resets pagination after changing %s',
  async change => {
    knowledge.catalog.mockImplementation(async filters => ({
      entries: [entry()],
      total: 50,
      start: Number(filters.start ?? 0),
    }));
    renderWith(<CatalogPanel onClose={() => {}} />);
    fireEvent.click(await screen.findByText('Next'));
    expect(await screen.findByText('2–2 of 50')).toBeTruthy();
    if (change === 'language') {
      fireEvent.change(screen.getByLabelText('Language'), {
        target: { value: 'eng' },
      });
    } else if (change === 'search') {
      fireEvent.change(screen.getByLabelText('Search the Kiwix catalogue'), {
        target: { value: 'Python' },
      });
      fireEvent.click(screen.getByText('Search'));
    } else {
      fireEvent.click(screen.getByText('Stack Exchange'));
    }
    expect(await screen.findByText('1–1 of 50')).toBeTruthy();
    expect(knowledge.catalog.mock.lastCall?.[0].start).toBeUndefined();
  }
);
