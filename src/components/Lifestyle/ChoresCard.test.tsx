// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type TodoItem } from '@/hooks/api';
import { ChoresCard } from './ChoresCard';

vi.mock('@/hooks/api', () => ({
  api: {
    todos: {
      list: vi.fn().mockResolvedValue([]),
      create: vi.fn().mockResolvedValue({ id: 'new' }),
      update: vi.fn().mockResolvedValue({ success: true }),
      remove: vi.fn().mockResolvedValue({ success: true }),
    },
  },
}));

const list = vi.mocked(api.todos.list);

const todo = (
  id: string,
  title: string,
  extra: Partial<TodoItem> = {}
): TodoItem => ({
  id,
  title,
  done: false,
  completedAt: null,
  list: 'chores',
  notes: null,
  due: null,
  repeatInterval: null,
  repeatUnit: null,
  priority: 3,
  createdAt: '',
  updatedAt: '',
  ...extra,
});

/** `n` days from now as an ISO timestamp, the shape the API returns `due` in. */
const inDays = (n: number) =>
  new Date(Date.now() + n * 86_400_000).toISOString();

beforeEach(() => {
  vi.clearAllMocks();
  list.mockResolvedValue([]);
});

function renderCard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ChoresCard />
    </QueryClientProvider>
  );
}

describe('which chores show', () => {
  it('shows only chores, not the other todo lists', async () => {
    list.mockResolvedValue([
      todo('c1', 'Clean the sink'),
      todo('t1', 'Fix the bike', { list: 'todo' }),
      todo('a1', 'Learn accordion', { list: 'archive' }),
    ]);
    renderCard();

    expect(await screen.findByText('Clean the sink')).toBeTruthy();
    expect(screen.queryByText('Fix the bike')).toBeNull();
    expect(screen.queryByText('Learn accordion')).toBeNull();
  });

  it('hides a repeating chore that is not due within ~10% of its interval', async () => {
    // A monthly chore 20 days out shouldn't sit here nagging for three weeks —
    // this is the Tasks view's isFarOffPeriodic rule, and the whole reason this
    // card renders through the shared partitionTodos instead of its own filter.
    list.mockResolvedValue([
      todo('c1', 'Descale the kettle', {
        repeatInterval: 1,
        repeatUnit: 'month',
        due: inDays(20),
      }),
    ]);
    renderCard();

    await waitFor(() => expect(list).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText('Nothing due.')).toBeTruthy());
    expect(screen.queryByText('Descale the kettle')).toBeNull();
  });

  it('shows the same chore once it comes due', async () => {
    list.mockResolvedValue([
      todo('c1', 'Descale the kettle', {
        repeatInterval: 1,
        repeatUnit: 'month',
        due: inDays(2),
      }),
    ]);
    renderCard();
    expect(await screen.findByText('Descale the kettle')).toBeTruthy();
  });

  it('never hides an overdue or non-repeating chore', async () => {
    list.mockResolvedValue([
      todo('c1', 'Take out recycling', {
        repeatInterval: 1,
        repeatUnit: 'month',
        due: inDays(-3),
      }),
      todo('c2', 'Wash the car', { due: inDays(200) }),
    ]);
    renderCard();

    expect(await screen.findByText('Take out recycling')).toBeTruthy();
    expect(screen.getByText('Wash the car')).toBeTruthy();
  });

  it('drops a completed chore out of the list', async () => {
    list.mockResolvedValue([
      todo('c1', 'Clean the sink', { done: true, completedAt: inDays(0) }),
    ]);
    renderCard();
    await waitFor(() => expect(screen.getByText('Nothing due.')).toBeTruthy());
  });
});

describe('the same rows the Tasks view edits', () => {
  it('renders the Tasks row chrome: due date, repeat and priority flag', async () => {
    list.mockResolvedValue([
      todo('c1', 'Change the filter', {
        due: '2026-07-30T12:00:00+00:00',
        repeatInterval: 2,
        repeatUnit: 'week',
        priority: 5,
      }),
    ]);
    renderCard();

    await screen.findByText('Change the filter');
    expect(screen.getByText(/every 2 weeks/)).toBeTruthy();
    expect(screen.getByText('⚑P5')).toBeTruthy();
    expect(screen.getByTitle('Very important')).toBeTruthy();
  });

  it('ticks a chore off through /api/tasks/todos', async () => {
    list.mockResolvedValue([todo('c1', 'Clean the sink')]);
    renderCard();
    await screen.findByText('Clean the sink');

    // The checkbox is the first button in the row.
    fireEvent.click(screen.getAllByRole('button')[1]);
    await waitFor(() =>
      expect(api.todos.update).toHaveBeenCalledWith('c1', { done: true })
    );
  });

  it('deletes a chore through the row’s delete affordance', async () => {
    list.mockResolvedValue([todo('c1', 'Clean the sink')]);
    renderCard();
    await screen.findByText('Clean the sink');

    fireEvent.click(screen.getByTitle('Delete'));
    await waitFor(() => expect(api.todos.remove).toHaveBeenCalledWith('c1'));
  });

  it('adds a chore with a repeat interval, so one can be made periodic here', async () => {
    renderCard();
    fireEvent.click(screen.getByRole('button', { name: /add chore/i }));

    fireEvent.change(screen.getByPlaceholderText('Title…'), {
      target: { value: 'Vacuum' },
    });
    // The "Every <n> <unit>" repeat field.
    fireEvent.change(screen.getByPlaceholderText('—'), {
      target: { value: '2' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));

    await waitFor(() => expect(api.todos.create).toHaveBeenCalledTimes(1));
    expect(api.todos.create).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Vacuum',
        list: 'chores',
        repeatInterval: 2,
        repeatUnit: 'week',
      })
    );
  });
});
