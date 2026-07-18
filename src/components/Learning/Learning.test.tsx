// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ShortcutProvider } from '../../shortcuts/ShortcutProvider';
import { Learning } from './Learning';

const { mocks } = vi.hoisted(() => {
  const mocks = {
    getStats: vi.fn(),
    getTags: vi.fn(),
    listFolders: vi.fn(),
    listQueue: vi.fn(),
    getDue: vi.fn(),
    listCards: vi.fn(),
    listMcpServers: vi.fn(),
    grade: vi.fn(),
    review: vi.fn(),
    generate: vi.fn(),
    createCard: vi.fn(),
    approve: vi.fn(),
    regenerate: vi.fn(),
    deny: vi.fn(),
    deleteCard: vi.fn(),
    createFolder: vi.fn(),
    updateFolder: vi.fn(),
    deleteFolder: vi.fn(),
  };
  return { mocks };
});

vi.mock('../../hooks/api', () => ({
  api: {
    learning: mocks,
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
  },
}));

function renderLearning() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="learning" onViewChange={() => {}}>
        <Learning />
      </ShortcutProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getStats.mockResolvedValue({ total: 10, due: 3, pending: 2, mastered: 4, learning: 6 });
  mocks.getTags.mockResolvedValue([{ name: 'python', count: 5 }]);
  mocks.listFolders.mockResolvedValue([{
    id: 'f1', name: 'Python', position: 0, evidenceProviderId: null, evidenceProviderName: null,
    activeCount: 5, pendingCount: 0, dueCount: 2, createdAt: '', updatedAt: '',
  }]);
  mocks.listQueue.mockResolvedValue([]);
  mocks.getDue.mockResolvedValue([]);
  mocks.listCards.mockResolvedValue([]);
  mocks.listMcpServers.mockResolvedValue([]);
});

describe('Learning', () => {
  it('renders stats, folder pills, and the due count in the Review tab', async () => {
    renderLearning();
    expect(await screen.findByText('Review (3)')).toBeTruthy();
    expect(await screen.findByText('In Queue')).toBeTruthy();
    expect(await screen.findByText('Python')).toBeTruthy();
    expect(screen.getByText('#python')).toBeTruthy();
  });

  it('switches between modes', async () => {
    renderLearning();
    fireEvent.click(await screen.findByText('Browse'));
    expect(await screen.findByText('No cards here yet')).toBeTruthy();
    fireEvent.click(screen.getByText('Queue'));
    expect(await screen.findByText('The approval queue is empty')).toBeTruthy();
    fireEvent.click(screen.getByText('+ Create'));
    expect(await screen.findByText('Generate Cards')).toBeTruthy();
    fireEvent.click(screen.getByText('Folders'));
    expect(await screen.findByText('Evidence providers (MCP servers)')).toBeTruthy();
  });

  it('cycles modes with the keyboard after drilling in', async () => {
    renderLearning();
    await screen.findByText('Review (3)');

    fireEvent.keyDown(window, { code: 'KeyD' }); // sidebar level -> mode bar
    fireEvent.keyDown(window, { code: 'KeyS' }); // review -> queue
    expect(await screen.findByText('The approval queue is empty')).toBeTruthy();
    fireEvent.keyDown(window, { code: 'KeyS' }); // queue -> browse
    expect(await screen.findByText('No cards here yet')).toBeTruthy();
    fireEvent.keyDown(window, { code: 'KeyW' }); // browse -> queue
    expect(await screen.findByText('The approval queue is empty')).toBeTruthy();
  });

  it('N jumps to the Create (brain-dump) mode', async () => {
    renderLearning();
    await screen.findByText('Review (3)');

    fireEvent.keyDown(window, { code: 'KeyN' });
    expect(await screen.findByText('Generate Cards')).toBeTruthy();
  });
});
