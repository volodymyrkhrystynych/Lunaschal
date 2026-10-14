// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type ChatTodoItem } from '../../hooks/api';
import { ChatTodoBar } from './ChatTodoBar';

vi.mock('../../hooks/api', () => ({
  api: {
    chatTodos: {
      list: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
      promote: vi.fn(),
    },
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
});

function todo(overrides: Partial<ChatTodoItem> = {}): ChatTodoItem {
  return {
    id: 't1',
    dayKey: '2026-08-25',
    title: 'Call the dentist',
    notes: null,
    due: null,
    priority: 3,
    done: false,
    completedAt: null,
    createdAt: '2026-08-25T08:00:00.000Z',
    updatedAt: '2026-08-25T08:00:00.000Z',
    ...overrides,
  };
}

function renderBar() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ChatTodoBar />
    </QueryClientProvider>
  );
}

describe('collapsed by default', () => {
  it('starts collapsed and shows a count, not the list', async () => {
    vi.mocked(api.chatTodos.list).mockResolvedValue([
      todo(),
      todo({ id: 't2' }),
    ]);
    renderBar();

    expect(await screen.findByText('2 to-dos today')).toBeTruthy();
    expect(screen.queryByText('Call the dentist')).toBeNull();
  });

  it('expands to show the list on click', async () => {
    vi.mocked(api.chatTodos.list).mockResolvedValue([todo()]);
    renderBar();

    fireEvent.click(await screen.findByText('1 to-do today'));
    expect(await screen.findByText('Call the dentist')).toBeTruthy();
  });
});

describe('quick edit', () => {
  it('saves an inline title edit', async () => {
    vi.mocked(api.chatTodos.list).mockResolvedValue([todo()]);
    vi.mocked(api.chatTodos.update).mockResolvedValue({ success: true });
    renderBar();

    fireEvent.click(await screen.findByText('1 to-do today'));
    fireEvent.click(await screen.findByText('Call the dentist'));
    const input = screen.getByDisplayValue('Call the dentist');
    fireEvent.change(input, { target: { value: 'Call the vet' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() =>
      expect(api.chatTodos.update).toHaveBeenCalledWith('t1', {
        title: 'Call the vet',
      })
    );
  });

  it('toggles done', async () => {
    vi.mocked(api.chatTodos.list).mockResolvedValue([todo()]);
    vi.mocked(api.chatTodos.update).mockResolvedValue({ success: true });
    renderBar();

    fireEvent.click(await screen.findByText('1 to-do today'));
    const checkbox = (await screen.findByText('Call the dentist'))
      .closest('div')!
      .parentElement!.querySelector('button')!;
    fireEvent.click(checkbox);

    await waitFor(() =>
      expect(api.chatTodos.update).toHaveBeenCalledWith('t1', { done: true })
    );
  });

  it('dismisses a to-do', async () => {
    vi.mocked(api.chatTodos.list).mockResolvedValue([todo()]);
    vi.mocked(api.chatTodos.remove).mockResolvedValue({ success: true });
    renderBar();

    fireEvent.click(await screen.findByText('1 to-do today'));
    fireEvent.click(await screen.findByTitle('Dismiss'));

    await waitFor(() =>
      expect(api.chatTodos.remove).toHaveBeenCalledWith('t1')
    );
  });
});

describe('send to permanent', () => {
  it('toggling send mode then clicking the title expands the full editor', async () => {
    vi.mocked(api.chatTodos.list).mockResolvedValue([todo()]);
    renderBar();

    fireEvent.click(await screen.findByText('1 to-do today'));
    fireEvent.click(screen.getByTitle('Switch to send-to-permanent mode'));
    fireEvent.click(screen.getByText('Call the dentist'));

    expect(await screen.findByText('Save and send to permanent')).toBeTruthy();
    expect(screen.getByDisplayValue('Call the dentist')).toBeTruthy();
  });

  it('submitting the promote editor posts the full payload and removes the row', async () => {
    vi.mocked(api.chatTodos.list).mockResolvedValue([
      todo({ notes: 'ask about the filling' }),
    ]);
    vi.mocked(api.chatTodos.promote).mockResolvedValue({ id: 'todo1' });
    renderBar();

    fireEvent.click(await screen.findByText('1 to-do today'));
    fireEvent.click(screen.getByTitle('Switch to send-to-permanent mode'));
    fireEvent.click(screen.getByText('Call the dentist'));

    const dateInput = await screen.findByLabelText('Due date');
    fireEvent.change(dateInput, { target: { value: '2026-09-01' } });
    fireEvent.click(screen.getByText('Save and send to permanent'));

    await waitFor(() =>
      expect(api.chatTodos.promote).toHaveBeenCalledWith(
        't1',
        expect.objectContaining({
          title: 'Call the dentist',
          notes: 'ask about the filling',
          list: 'todo',
          priority: 3,
        })
      )
    );
  });
});
