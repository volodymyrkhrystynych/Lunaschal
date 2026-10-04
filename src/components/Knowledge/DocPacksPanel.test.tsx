// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type KnowledgeDocPack } from '@/hooks/api';
import { DocPacksPanel } from './DocPacksPanel';

vi.mock('@/hooks/api', () => ({
  api: {
    knowledge: {
      docPacks: vi.fn(),
      docPackVersions: vi.fn(),
      installDocPack: vi.fn(),
      uploadDocPack: vi.fn(),
      updateDocPack: vi.fn(),
      deleteDocPack: vi.fn(),
    },
  },
}));

const knowledge = api.knowledge as unknown as Record<
  | 'docPacks'
  | 'docPackVersions'
  | 'installDocPack'
  | 'uploadDocPack'
  | 'updateDocPack'
  | 'deleteDocPack',
  ReturnType<typeof vi.fn>
>;

function pack(over: Partial<KnowledgeDocPack> = {}): KnowledgeDocPack {
  return {
    id: 'docpack:01',
    registry: 'pip',
    name: 'flask',
    version: '3.1.3',
    description: '',
    sourceUrl: '',
    size: 864_256,
    chunkCount: 412,
    enabled: true,
    available: true,
    createdAt: 0,
    ...over,
  };
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DocPacksPanel />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  knowledge.docPacks.mockResolvedValue([]);
});

it('lists installed packages with their size and section count', async () => {
  knowledge.docPacks.mockResolvedValue([pack()]);
  renderPanel();
  expect(await screen.findByText('flask 3.1.3')).toBeTruthy();
  expect(screen.getByText(/pip · 412 sections · 844 KB/)).toBeTruthy();
});

it('says so when a package file has gone missing', async () => {
  knowledge.docPacks.mockResolvedValue([pack({ available: false })]);
  renderPanel();
  expect(await screen.findByText(/package file is missing/)).toBeTruthy();
});

it('looks a package up in the registry and installs the chosen version', async () => {
  knowledge.docPackVersions.mockResolvedValue([
    {
      registry: 'pip',
      name: 'flask',
      version: '3.1.3',
      description: '',
      size: 864_256,
      installed: false,
    },
    {
      registry: 'pip',
      name: 'flask',
      version: '3.0.3',
      description: '',
      size: null,
      installed: true,
    },
  ]);
  knowledge.installDocPack.mockResolvedValue(pack());
  renderPanel();

  fireEvent.click(await screen.findByText('Add docs'));
  fireEvent.change(screen.getByLabelText('Package registry'), {
    target: { value: 'pip' },
  });
  fireEvent.change(screen.getByLabelText('Package name'), {
    target: { value: ' flask ' },
  });
  fireEvent.click(screen.getByText('Find'));

  await waitFor(() =>
    expect(knowledge.docPackVersions).toHaveBeenCalledWith('pip', 'flask')
  );
  expect(
    (await screen.findByText('Installed')).closest('button')?.disabled
  ).toBe(true);
  fireEvent.click(screen.getByText('Install'));
  await waitFor(() =>
    expect(knowledge.installDocPack).toHaveBeenCalledWith(
      'pip',
      'flask',
      '3.1.3'
    )
  );
  // Installing refetches the installed list.
  await waitFor(() => expect(knowledge.docPacks).toHaveBeenCalledTimes(2));
});

it('tells the user when the registry has nothing under that name', async () => {
  knowledge.docPackVersions.mockResolvedValue([]);
  renderPanel();
  fireEvent.click(await screen.findByText('Add docs'));
  fireEvent.change(screen.getByLabelText('Package name'), {
    target: { value: 'nope' },
  });
  fireEvent.click(screen.getByText('Find'));
  expect(await screen.findByText(/no docs for npm\/nope/)).toBeTruthy();
});

it('toggles and removes an installed package', async () => {
  knowledge.docPacks.mockResolvedValue([pack()]);
  knowledge.updateDocPack.mockResolvedValue(pack({ enabled: false }));
  knowledge.deleteDocPack.mockResolvedValue({ deleted: true });
  renderPanel();

  fireEvent.click(await screen.findByLabelText('Search flask 3.1.3'));
  await waitFor(() =>
    expect(knowledge.updateDocPack).toHaveBeenCalledWith('docpack:01', {
      enabled: false,
    })
  );
  fireEvent.click(screen.getByText('Remove'));
  await waitFor(() =>
    expect(knowledge.deleteDocPack).toHaveBeenCalledWith('docpack:01')
  );
});

it('uploads a package file', async () => {
  knowledge.uploadDocPack.mockResolvedValue(pack({ registry: 'local' }));
  renderPanel();
  fireEvent.click(await screen.findByText('Add docs'));
  const file = new File(['x'], 'mylib.db');
  fireEvent.change(screen.getByLabelText('Upload docs package'), {
    target: { files: [file] },
  });
  await waitFor(() =>
    expect(knowledge.uploadDocPack).toHaveBeenCalledWith(file)
  );
});
