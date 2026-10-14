// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ShortcutProvider } from '../../shortcuts/ShortcutProvider';
import { Browse } from './Browse';

const { mocks } = vi.hoisted(() => ({
  mocks: {
    listCards: vi.fn(),
    unretireCard: vi.fn(),
    deleteCard: vi.fn(),
  },
}));

vi.mock('../../hooks/api', () => ({
  api: {
    learning: mocks,
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
  },
}));

const base = {
  folderId: null,
  answer: 'A.',
  tags: [],
  sourceType: null,
  sourceId: null,
  derivedFrom: null,
  revisedFrom: null,
  due: '2026-01-01T00:00:00Z',
  kind: 'qa',
  createdAt: '',
  updatedAt: '',
};

function renderBrowse() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="learning" onViewChange={() => {}}>
        <Browse folderId={null} tag={null} onSelectTag={() => {}} />
      </ShortcutProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listCards.mockImplementation(async (p: { state?: string }) =>
    p.state === 'retired'
      ? [
          {
            ...base,
            id: 'r1',
            question: 'Taken out',
            state: 'retired',
            superseded: false,
          },
          {
            ...base,
            id: 'r2',
            question: 'Replaced',
            state: 'retired',
            superseded: true,
          },
        ]
      : [{ ...base, id: 'a1', question: 'Live', state: 'active' }]
  );
  mocks.unretireCard.mockResolvedValue({ success: true });
});

describe('Browse retired cards', () => {
  it('lists retired cards on demand and unretires only the ones that can return', async () => {
    renderBrowse();
    expect(await screen.findByText('Live')).toBeTruthy();
    expect(screen.queryByText('Unretire')).toBeNull();

    fireEvent.click(screen.getByLabelText('Show retired cards'));
    expect(await screen.findByText('Taken out')).toBeTruthy();
    expect(screen.getByText('Old version')).toBeTruthy();
    expect(screen.queryByText('Edit')).toBeNull();

    const buttons = screen.getAllByText('Unretire');
    expect(buttons).toHaveLength(1);
    fireEvent.click(buttons[0]);
    await waitFor(() => expect(mocks.unretireCard.mock.calls[0][0]).toBe('r1'));
  });
});
