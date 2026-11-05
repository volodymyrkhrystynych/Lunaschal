// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type CanonSource } from '../../hooks/api';
import { CanonPanel } from './CanonPanel';

vi.mock('../../hooks/api', () => ({
  api: {
    writing: {
      listCanon: vi.fn(),
      projectCanon: vi.fn(),
      setProjectCanon: vi.fn(),
      fetchCanon: vi.fn(),
      uploadCanon: vi.fn(),
      deleteCanon: vi.fn(),
    },
  },
}));

const source = (over: Partial<CanonSource> = {}): CanonSource => ({
  id: 's1',
  name: 'Salt Roads Wiki',
  wikiHost: 'saltroads.fandom.com',
  sourceUrl: null,
  status: 'ready',
  pages: 1234,
  chunks: 5000,
  error: null,
  licence: 'CC BY-SA 3.0',
  createdAt: '',
  updatedAt: '',
  ...over,
});

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <CanonPanel projectId="p1" />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.mocked(api.writing.listCanon).mockResolvedValue([source()]);
  vi.mocked(api.writing.projectCanon).mockResolvedValue({ sourceIds: [] });
  vi.mocked(api.writing.setProjectCanon).mockReset();
  vi.mocked(api.writing.fetchCanon).mockReset();
});

describe('CanonPanel', () => {
  it('links a ready source to this project', async () => {
    vi.mocked(api.writing.setProjectCanon).mockResolvedValue({
      sourceIds: ['s1'],
    });
    renderPanel();

    expect(await screen.findByText('1,234 pages')).not.toBeNull();
    fireEvent.click(screen.getByLabelText(/Salt Roads Wiki/));

    await waitFor(() =>
      expect(api.writing.setProjectCanon).toHaveBeenCalledWith('p1', ['s1'])
    );
  });

  it('says why an import failed', async () => {
    vi.mocked(api.writing.listCanon).mockResolvedValue([
      source({ status: 'error', error: 'That wiki has no dump' }),
    ]);
    renderPanel();
    expect(await screen.findByText('That wiki has no dump')).not.toBeNull();
  });

  it('imports a wiki by URL and links it straight away', async () => {
    vi.mocked(api.writing.fetchCanon).mockResolvedValue({ id: 's2' });
    vi.mocked(api.writing.setProjectCanon).mockResolvedValue({
      sourceIds: ['s2'],
    });
    renderPanel();

    fireEvent.click(await screen.findByTitle('Import a fandom wiki'));
    fireEvent.change(screen.getByLabelText('Wiki or dump URL'), {
      target: { value: 'https://saltroads.fandom.com' },
    });
    fireEvent.click(screen.getByText('Import'));

    await waitFor(() =>
      expect(api.writing.fetchCanon).toHaveBeenCalledWith(
        'https://saltroads.fandom.com'
      )
    );
    await waitFor(() =>
      expect(api.writing.setProjectCanon).toHaveBeenCalledWith('p1', ['s2'])
    );
  });
});
