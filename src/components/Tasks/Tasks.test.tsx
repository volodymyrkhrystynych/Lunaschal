// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ShortcutProvider } from '../../shortcuts/ShortcutProvider';
import { api, DailyTask, TodoItem } from '../../hooks/api';
import { dueInputToUnix } from '../../lib/todos';
import { TasksSection } from './index';

vi.mock('../../hooks/api', () => ({
  api: {
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
    tasks: {
      list: vi.fn().mockResolvedValue([]),
      create: vi.fn().mockResolvedValue({ id: 'nd' }),
      update: vi.fn().mockResolvedValue({ success: true }),
      reorder: vi.fn().mockResolvedValue({ success: true }),
      remove: vi.fn().mockResolvedValue({ success: true }),
      complete: vi.fn().mockResolvedValue({ success: true }),
      uncomplete: vi.fn().mockResolvedValue({ success: true }),
    },
    todos: {
      list: vi.fn().mockResolvedValue([]),
      create: vi.fn().mockResolvedValue({ id: 'nt' }),
      update: vi.fn().mockResolvedValue({ success: true }),
      remove: vi.fn().mockResolvedValue({ success: true }),
    },
  },
}));

const dailyTask = (id: string, title: string, position: number): DailyTask => ({
  id,
  title,
  position,
  done: false,
  createdAt: '',
  updatedAt: '',
});

// `list` is a bare string so a fixture can carry the retired 'chores' value.
const todo = (
  id: string,
  title: string,
  list: string,
  extra: Partial<TodoItem> = {}
): TodoItem => ({
  id,
  title,
  done: false,
  completedAt: null,
  list: list as TodoItem['list'],
  notes: null,
  due: null,
  repeatInterval: null,
  repeatUnit: null,
  priority: 3,
  createdAt: '',
  updatedAt: '',
  ...extra,
});

const fixtures = {
  tasks: [dailyTask('d1', 'Morning pages', 1), dailyTask('d2', 'Stretch', 2)],
  todos: [
    todo('t1', 'Fix the bike', 'todo', {
      notes: 'rear brake pads are worn down',
      due: '2020-01-05T12:00:00+00:00', // long past → overdue
    }),
    todo('t2', 'Water plants', 'todo', {
      repeatInterval: 2,
      repeatUnit: 'week',
    }),
    todo('c1', 'Clean the sink', 'chores'), // retired list, migrated server-side
    todo('a1', 'Learn accordion', 'archive'),
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
  vi.mocked(api.tasks.list).mockResolvedValue(fixtures.tasks);
  vi.mocked(api.todos.list).mockResolvedValue(fixtures.todos);
});

function renderTasks() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="lifestyle" onViewChange={() => {}}>
        <TasksSection />
      </ShortcutProvider>
    </QueryClientProvider>
  );
}

const key = (code: string) => fireEvent.keyDown(window, { code });

describe('list rendering and switching', () => {
  it('shows daily tasks and only the To-Do list by default', async () => {
    renderTasks();
    expect(await screen.findByText('Morning pages')).not.toBeNull();
    expect(await screen.findByText('Fix the bike')).not.toBeNull();
    expect(screen.queryByText('Learn accordion')).toBeNull();
  });

  it('shows a row from the retired chores list among the to-dos', async () => {
    // The rows were folded into 'todo' by the migration, but one queued offline
    // against the old build still arrives naming it — it must not go invisible.
    renderTasks();
    expect(await screen.findByText('Clean the sink')).not.toBeNull();
  });

  it('switches the visible list when a pill is clicked', async () => {
    renderTasks();
    await screen.findByText('Fix the bike');

    fireEvent.click(screen.getByRole('button', { name: /Archive/ }));
    expect(await screen.findByText('Learn accordion')).not.toBeNull();
    expect(screen.queryByText('Fix the bike')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /To-Do/ }));
    expect(await screen.findByText('Fix the bike')).not.toBeNull();
  });

  it('renders due, repeat, and truncated notes on rows', async () => {
    renderTasks();
    await screen.findByText('Fix the bike');

    const notes = screen.getByText('rear brake pads are worn down');
    expect(notes.className).toContain('truncate');
    expect(screen.getByText(/Jan 5, 2020/).className).toContain('text-red-400');
    expect(screen.getByText(/every 2 weeks/)).not.toBeNull();
  });
});

describe('level-1 section selection', () => {
  it('moves the highlight from Daily through the list pills with S', async () => {
    renderTasks();
    await screen.findByText('Fix the bike');

    key('KeyD'); // level 0 → 1, Daily focused
    expect(screen.getByText('Daily Tasks').className).toContain('ring-1');

    key('KeyS'); // → To-Do
    expect(screen.getByText('Daily Tasks').className).not.toContain('ring-1');
    expect(screen.getByRole('button', { name: /To-Do/ }).className).toContain(
      'ring-1'
    );

    key('KeyS'); // → Archive (also switches the visible list)
    expect(screen.getByRole('button', { name: /Archive/ }).className).toContain(
      'ring-1'
    );
    expect(await screen.findByText('Learn accordion')).not.toBeNull();
  });
});

describe('todo creation', () => {
  it('drilling into To-Do opens the form with the title focused and Ctrl+Enter creates', async () => {
    renderTasks();
    await screen.findByText('Fix the bike');

    key('KeyD');
    key('KeyS'); // To-Do selected at level 1
    key('KeyD'); // drill in → create form

    const title = screen.getByPlaceholderText('Title…');
    expect(document.activeElement).toBe(title);

    fireEvent.change(title, { target: { value: 'Descale kettle' } });
    fireEvent.keyDown(title, { key: 'Enter', ctrlKey: true });

    await waitFor(() => expect(api.todos.create).toHaveBeenCalled());
    expect(vi.mocked(api.todos.create).mock.calls[0][0]).toEqual({
      // Client-generated ULID for idempotent offline replay.
      id: expect.any(String),
      title: 'Descale kettle',
      list: 'todo',
      notes: undefined,
      due: null,
      repeatInterval: undefined,
      repeatUnit: undefined,
      priority: 3,
    });
  });

  it('cycles through all six fields with Tab and back with Shift+Tab', async () => {
    renderTasks();
    await screen.findByText('Fix the bike');

    key('KeyD');
    key('KeyS'); // To-Do
    key('KeyD'); // open form

    const title = screen.getByPlaceholderText('Title…');
    const notes = screen.getByPlaceholderText('More information…');
    expect(document.activeElement).toBe(title);

    fireEvent.keyDown(title, { key: 'Tab' });
    expect(document.activeElement).toBe(notes);
    fireEvent.keyDown(notes, { key: 'Tab' });
    const dueInput = document.activeElement as HTMLInputElement;
    expect(dueInput.type).toBe('date');
    fireEvent.keyDown(dueInput, { key: 'Tab' });
    const repeatN = document.activeElement as HTMLInputElement;
    expect(repeatN.type).toBe('number');
    fireEvent.keyDown(repeatN, { key: 'Tab' });
    const unit = document.activeElement as HTMLSelectElement;
    expect(unit.tagName).toBe('SELECT');
    expect(unit.value).toBe('week');
    fireEvent.keyDown(unit, { key: 'Tab' });
    const priority = document.activeElement as HTMLSelectElement;
    expect(priority.tagName).toBe('SELECT');
    expect(priority.value).toBe('3');
    fireEvent.keyDown(priority, { key: 'Tab' }); // wraps around
    expect(document.activeElement).toBe(title);

    fireEvent.keyDown(title, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(priority);
  });

  it('submits the full payload with Ctrl+Enter from a non-title field', async () => {
    renderTasks();
    await screen.findByText('Fix the bike');

    key('KeyD');
    key('KeyS');
    key('KeyD');

    const title = screen.getByPlaceholderText('Title…');
    const notes = screen.getByPlaceholderText('More information…');
    fireEvent.change(title, { target: { value: 'Renew passport' } });
    fireEvent.change(notes, { target: { value: 'photos first' } });
    fireEvent.keyDown(title, { key: 'Tab' });
    fireEvent.keyDown(notes, { key: 'Tab' });
    const dueInput = document.activeElement as HTMLInputElement;
    fireEvent.change(dueInput, { target: { value: '2026-07-25' } });
    fireEvent.keyDown(dueInput, { key: 'Tab' });
    const repeatN = document.activeElement as HTMLInputElement;
    fireEvent.change(repeatN, { target: { value: '3' } });
    fireEvent.keyDown(repeatN, { key: 'Enter', ctrlKey: true });

    await waitFor(() => expect(api.todos.create).toHaveBeenCalled());
    expect(vi.mocked(api.todos.create).mock.calls[0][0]).toEqual({
      // Client-generated ULID for idempotent offline replay.
      id: expect.any(String),
      title: 'Renew passport',
      list: 'todo',
      notes: 'photos first',
      due: dueInputToUnix('2026-07-25'),
      repeatInterval: 3,
      repeatUnit: 'week',
      priority: 3,
    });
  });

  it('Escape leaves the form into item navigation; S steps and expands notes', async () => {
    renderTasks();
    await screen.findByText('Fix the bike');

    key('KeyD');
    key('KeyS'); // To-Do
    key('KeyD'); // open form
    const title = screen.getByPlaceholderText('Title…');
    fireEvent.keyDown(title, { key: 'Escape' });

    expect(screen.queryByPlaceholderText('Title…')).toBeNull();
    const first = document.getElementById('todo-row-t1')!;
    expect(first.className).toContain('ring-1');
    // Selected row's notes are expanded, not truncated
    const notes = screen.getByText('rear brake pads are worn down');
    expect(notes.className).not.toContain('truncate');

    key('KeyS');
    expect(first.className).not.toContain('ring-1');
    expect(document.getElementById('todo-row-t2')!.className).toContain(
      'ring-1'
    );
    expect(notes.className).toContain('truncate');
  });

  it('drilling into Archive enters item navigation without a form', async () => {
    renderTasks();
    await screen.findByText('Fix the bike');

    key('KeyD');
    key('KeyS');
    key('KeyS'); // Archive
    await screen.findByText('Learn accordion');
    key('KeyD');

    expect(screen.queryByPlaceholderText('Title…')).toBeNull();
    expect(document.getElementById('todo-row-a1')!.className).toContain(
      'ring-1'
    );
  });
});

describe('Q moves between To-Do and Archive', () => {
  it('archives a selected todo and un-archives from the Archive list', async () => {
    renderTasks();
    await screen.findByText('Fix the bike');

    key('KeyD');
    key('KeyS'); // To-Do
    key('KeyD'); // form
    fireEvent.keyDown(screen.getByPlaceholderText('Title…'), {
      key: 'Escape',
    });
    key('KeyQ');
    await waitFor(() =>
      expect(api.todos.update).toHaveBeenCalledWith('t1', { list: 'archive' })
    );

    key('KeyA'); // back to level 1
    key('KeyS'); // Archive
    await screen.findByText('Learn accordion');
    key('KeyD');
    key('KeyQ');
    await waitFor(() =>
      expect(api.todos.update).toHaveBeenCalledWith('a1', { list: 'todo' })
    );
  });

  it('does nothing on a daily task', async () => {
    renderTasks();
    await screen.findByText('Morning pages');

    key('KeyD'); // Daily at level 1
    key('KeyD'); // item nav
    key('KeyQ');
    expect(api.todos.update).not.toHaveBeenCalled();
  });
});

describe('E toggles completion', () => {
  it('completes the selected daily task', async () => {
    renderTasks();
    await screen.findByText('Morning pages');

    key('KeyD'); // Daily focused at level 1
    key('KeyD'); // item nav
    expect(document.getElementById('todo-row-d1')!.className).toContain(
      'ring-1'
    );
    key('KeyE');
    await waitFor(() => expect(api.tasks.complete).toHaveBeenCalledWith('d1'));
  });

  it('marks the selected todo done', async () => {
    renderTasks();
    await screen.findByText('Fix the bike');

    key('KeyD');
    key('KeyS');
    key('KeyD');
    fireEvent.keyDown(screen.getByPlaceholderText('Title…'), {
      key: 'Escape',
    });
    key('KeyE');
    await waitFor(() =>
      expect(api.todos.update).toHaveBeenCalledWith('t1', { done: true })
    );
  });
});
