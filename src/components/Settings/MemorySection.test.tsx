// @vitest-environment jsdom
/**
 * The memory editor is what makes the assistant's unconfirmed `remember` write
 * acceptable — so what matters here is that a change is visible, that an edit
 * in progress can't be clobbered by one, and that any version is one click from
 * coming back.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { MemorySection } from './MemorySection';

vi.mock('../../hooks/api', () => ({
  api: {
    memory: {
      get: vi.fn(),
      update: vi.fn(),
      revisions: vi.fn(),
      restore: vi.fn(),
    },
  },
}));

const renderSection = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemorySection />
    </QueryClientProvider>
  );
  return client;
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.mocked(api.memory.get).mockResolvedValue({
    content: '- Their gym is Movati',
    maxChars: 4000,
  });
  vi.mocked(api.memory.update).mockResolvedValue({
    content: '- Their gym is GoodLife',
    maxChars: 4000,
  });
  vi.mocked(api.memory.revisions).mockResolvedValue([
    {
      id: 'r1',
      content: '- the earlier version',
      source: 'remember',
      note: 'Their gym is Movati',
      createdAt: '2026-01-01T08:00:00.000Z',
    },
  ]);
  vi.mocked(api.memory.restore).mockResolvedValue({
    content: '- the earlier version',
    maxChars: 4000,
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const box = async () =>
  (await screen.findByLabelText('Memory')) as HTMLTextAreaElement;

it('shows the current document', async () => {
  renderSection();
  await waitFor(async () =>
    expect((await box()).value).toBe('- Their gym is Movati')
  );
});

it('autosaves an edit after the debounce, not on every keystroke', async () => {
  renderSection();
  fireEvent.change(await box(), {
    target: { value: '- Their gym is GoodLife' },
  });
  expect(api.memory.update).not.toHaveBeenCalled();

  vi.advanceTimersByTime(1500);
  await waitFor(() =>
    expect(api.memory.update).toHaveBeenCalledWith('- Their gym is GoodLife')
  );
});

it('does not clobber an in-progress edit when the document changes underneath', async () => {
  // The assistant can write to this while the box is open — a refetch landing
  // mid-sentence must not take the sentence away.
  const client = renderSection();
  await waitFor(async () => expect((await box()).value).not.toBe(''));

  fireEvent.change(await box(), { target: { value: 'half a thought' } });
  vi.mocked(api.memory.get).mockResolvedValue({
    content: '- something the assistant just added',
    maxChars: 4000,
  });
  await client.invalidateQueries({ queryKey: ['memory'] });

  expect((await box()).value).toBe('half a thought');
});

it('counts against the cap and flags going over it', async () => {
  renderSection();
  expect(await screen.findByText('21 / 4000 characters')).toBeTruthy();

  fireEvent.change(await box(), { target: { value: 'x'.repeat(4001) } });
  const counter = await screen.findByText('4001 / 4000 characters');
  expect(counter.className).toContain('red');
});

it('lists what changed and restores an earlier version', async () => {
  renderSection();
  fireEvent.click(await screen.findByRole('button', { name: 'History' }));

  // Named by who changed it and why — an assistant write has to be identifiable
  // as one.
  expect(
    await screen.findByText(/assistant added a line: Their gym is Movati/)
  ).toBeTruthy();

  fireEvent.click(screen.getByRole('button', { name: 'Restore' }));
  await waitFor(() => expect(api.memory.restore).toHaveBeenCalledWith('r1'));
  await waitFor(async () =>
    expect((await box()).value).toBe('- the earlier version')
  );
});

describe('an empty memory', () => {
  it('says nothing has changed yet', async () => {
    vi.mocked(api.memory.get).mockResolvedValue({
      content: '',
      maxChars: 4000,
    });
    vi.mocked(api.memory.revisions).mockResolvedValue([]);
    renderSection();
    fireEvent.click(await screen.findByRole('button', { name: 'History' }));
    expect(await screen.findByText('No changes yet.')).toBeTruthy();
  });
});
