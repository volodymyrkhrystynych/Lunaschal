import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, DailyTask, TodoItem } from '../hooks/api';
import { partitionTodos, formatCompletedAt } from '../lib/todos';

export function Tasks() {
  const [newTitle, setNewTitle] = useState('');
  const [showAdd, setShowAdd] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const queryClient = useQueryClient();

  const { data: tasks = [], isLoading } = useQuery({
    queryKey: ['tasks'],
    queryFn: api.tasks.list,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['tasks'] });

  const createTask = useMutation({
    mutationFn: (title: string) => api.tasks.create(title),
    onSuccess: () => { invalidate(); setNewTitle(''); setShowAdd(false); },
  });

  const updateTask = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => api.tasks.update(id, title),
    onSuccess: () => { invalidate(); setEditingId(null); },
  });

  const reorderTasks = useMutation({
    mutationFn: (order: string[]) => api.tasks.reorder(order),
    onSuccess: invalidate,
  });

  const deleteTask = useMutation({
    mutationFn: (id: string) => api.tasks.remove(id),
    onSuccess: invalidate,
  });

  const toggleComplete = useMutation({
    mutationFn: ({ id, done }: { id: string; done: boolean }) =>
      done ? api.tasks.uncomplete(id) : api.tasks.complete(id),
    onSuccess: invalidate,
  });

  const sorted = [...tasks].sort((a, b) => a.position - b.position);

  const moveTask = (index: number, direction: -1 | 1) => {
    const swapped = [...sorted];
    const target = index + direction;
    if (target < 0 || target >= swapped.length) return;
    [swapped[index], swapped[target]] = [swapped[target], swapped[index]];
    reorderTasks.mutate(swapped.map((t) => t.id));
  };

  const startEdit = (task: DailyTask) => {
    setEditingId(task.id);
    setEditTitle(task.title);
  };

  const saveEdit = () => {
    if (!editingId) return;
    const trimmed = editTitle.trim();
    if (trimmed) updateTask.mutate({ id: editingId, title: trimmed });
    else setEditingId(null);
  };

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-xl font-semibold text-[var(--color-text)]">Daily Tasks</h2>
          {tasks.length < 4 && !showAdd && (
            <button
              onClick={() => setShowAdd(true)}
              className="text-sm px-3 py-1.5 rounded border border-white/20 text-[var(--color-text)] hover:bg-white/10 transition-colors"
            >
              + Add task
            </button>
          )}
        </div>

        {isLoading && (
          <div className="text-[var(--color-text-muted)] text-sm">Loading…</div>
        )}

        <div className="space-y-2">
          {sorted.map((task, index) => (
            <div
              key={task.id}
              className={`flex items-center gap-3 p-3 rounded-lg border transition-colors ${
                task.done
                  ? 'border-white/5 bg-white/3 opacity-60'
                  : 'border-white/10 bg-[var(--color-surface)]'
              }`}
            >
              <span className="text-xs font-mono text-[var(--color-text-muted)] w-4 shrink-0">
                {task.position}
              </span>

              <button
                onClick={() => toggleComplete.mutate({ id: task.id, done: task.done })}
                className={`w-5 h-5 rounded border shrink-0 flex items-center justify-center transition-colors ${
                  task.done
                    ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                    : 'border-white/30 hover:border-white/50'
                }`}
              >
                {task.done && <span className="text-xs">✓</span>}
              </button>

              <div className="flex-1 min-w-0">
                {editingId === task.id ? (
                  <input
                    autoFocus
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    onBlur={saveEdit}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') saveEdit();
                      if (e.key === 'Escape') setEditingId(null);
                    }}
                    className="w-full bg-transparent text-[var(--color-text)] text-sm outline-none border-b border-[var(--color-primary)]"
                  />
                ) : (
                  <span
                    onClick={() => startEdit(task)}
                    className={`text-sm cursor-text select-none ${
                      task.done
                        ? 'line-through text-[var(--color-text-muted)]'
                        : 'text-[var(--color-text)]'
                    }`}
                  >
                    {task.title}
                  </span>
                )}
              </div>

              <div className="flex items-center gap-1 shrink-0">
                <button
                  onClick={() => moveTask(index, -1)}
                  disabled={index === 0}
                  className="p-1 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/10 disabled:opacity-20 transition-colors text-xs"
                  title="Move up"
                >
                  ↑
                </button>
                <button
                  onClick={() => moveTask(index, 1)}
                  disabled={index === sorted.length - 1}
                  className="p-1 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/10 disabled:opacity-20 transition-colors text-xs"
                  title="Move down"
                >
                  ↓
                </button>
                <button
                  onClick={() => deleteTask.mutate(task.id)}
                  className="p-1 rounded text-[var(--color-text-muted)] hover:text-red-400 hover:bg-white/10 transition-colors text-xs ml-1"
                  title="Delete"
                >
                  ✕
                </button>
              </div>
            </div>
          ))}

          {tasks.length === 0 && !showAdd && !isLoading && (
            <div className="text-center py-12 text-[var(--color-text-muted)] text-sm">
              No tasks yet. Add up to 4, ordered by importance.
            </div>
          )}
        </div>

        {showAdd && (
          <div className="mt-3 flex gap-2">
            <input
              autoFocus
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && newTitle.trim()) createTask.mutate(newTitle.trim());
                if (e.key === 'Escape') { setShowAdd(false); setNewTitle(''); }
              }}
              placeholder="Task title…"
              className="flex-1 bg-[var(--color-surface)] border border-white/20 rounded px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] outline-none focus:border-[var(--color-primary)]"
            />
            <button
              onClick={() => { if (newTitle.trim()) createTask.mutate(newTitle.trim()); }}
              disabled={!newTitle.trim() || createTask.isPending}
              className="px-3 py-2 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-40 transition-colors text-sm"
            >
              Add
            </button>
            <button
              onClick={() => { setShowAdd(false); setNewTitle(''); }}
              className="px-3 py-2 rounded text-[var(--color-text-muted)] hover:bg-white/10 transition-colors text-sm"
            >
              Cancel
            </button>
          </div>
        )}

        <TodoSection />
      </div>
    </div>
  );
}

function TodoSection() {
  const [newTitle, setNewTitle] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [showCompleted, setShowCompleted] = useState(false);
  const queryClient = useQueryClient();

  const { data: todos = [], isLoading } = useQuery({
    queryKey: ['todos'],
    queryFn: api.todos.list,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['todos'] });

  const createTodo = useMutation({
    mutationFn: (title: string) => api.todos.create(title),
    onSuccess: () => { invalidate(); setNewTitle(''); },
  });

  const updateTodo = useMutation({
    mutationFn: ({ id, ...data }: { id: string; title?: string; done?: boolean }) =>
      api.todos.update(id, data),
    onSuccess: () => { invalidate(); setEditingId(null); },
  });

  const deleteTodo = useMutation({
    mutationFn: (id: string) => api.todos.remove(id),
    onSuccess: invalidate,
  });

  const startEdit = (todo: TodoItem) => {
    setEditingId(todo.id);
    setEditTitle(todo.title);
  };

  const saveEdit = () => {
    if (!editingId) return;
    const trimmed = editTitle.trim();
    if (trimmed) updateTodo.mutate({ id: editingId, title: trimmed });
    else setEditingId(null);
  };

  const { active, completed } = partitionTodos(todos);

  const renderTodo = (todo: TodoItem) => (
    <div
      key={todo.id}
      className={`flex items-center gap-3 p-3 rounded-lg border transition-colors ${
        todo.done
          ? 'border-white/5 bg-white/3 opacity-60'
          : 'border-white/10 bg-[var(--color-surface)]'
      }`}
    >
      <button
        onClick={() => updateTodo.mutate({ id: todo.id, done: !todo.done })}
        className={`w-5 h-5 rounded border shrink-0 flex items-center justify-center transition-colors ${
          todo.done
            ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
            : 'border-white/30 hover:border-white/50'
        }`}
      >
        {todo.done && <span className="text-xs">✓</span>}
      </button>

      <div className="flex-1 min-w-0">
        {editingId === todo.id ? (
          <input
            autoFocus
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            onBlur={saveEdit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') saveEdit();
              if (e.key === 'Escape') setEditingId(null);
            }}
            className="w-full bg-transparent text-[var(--color-text)] text-sm outline-none border-b border-[var(--color-primary)]"
          />
        ) : (
          <span
            onClick={() => startEdit(todo)}
            className={`text-sm cursor-text select-none ${
              todo.done
                ? 'line-through text-[var(--color-text-muted)]'
                : 'text-[var(--color-text)]'
            }`}
          >
            {todo.title}
          </span>
        )}
      </div>

      {todo.done && todo.completedAt && (
        <span className="text-xs text-[var(--color-text-muted)] shrink-0">
          {formatCompletedAt(todo.completedAt)}
        </span>
      )}

      <button
        onClick={() => deleteTodo.mutate(todo.id)}
        className="p-1 rounded text-[var(--color-text-muted)] hover:text-red-400 hover:bg-white/10 transition-colors text-xs shrink-0"
        title="Delete"
      >
        ✕
      </button>
    </div>
  );

  return (
    <div className="mt-10">
      <h2 className="text-xl font-semibold text-[var(--color-text)] mb-6">To-Do</h2>

      {isLoading && (
        <div className="text-[var(--color-text-muted)] text-sm">Loading…</div>
      )}

      <div className="space-y-2">
        {active.map(renderTodo)}

        {active.length === 0 && !isLoading && (
          <div className="text-center py-8 text-[var(--color-text-muted)] text-sm">
            {completed.length > 0
              ? `All done — ${completed.length} completed.`
              : 'Nothing on the list.'}
          </div>
        )}
      </div>

      <div className="mt-3 flex gap-2">
        <input
          value={newTitle}
          onChange={(e) => setNewTitle(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && newTitle.trim()) createTodo.mutate(newTitle.trim());
            if (e.key === 'Escape') setNewTitle('');
          }}
          placeholder="Add a to-do…"
          className="flex-1 bg-[var(--color-surface)] border border-white/20 rounded px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] outline-none focus:border-[var(--color-primary)]"
        />
        <button
          onClick={() => { if (newTitle.trim()) createTodo.mutate(newTitle.trim()); }}
          disabled={!newTitle.trim() || createTodo.isPending}
          className="px-3 py-2 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-40 transition-colors text-sm"
        >
          Add
        </button>
      </div>

      {completed.length > 0 && (
        <div className="mt-6">
          <button
            onClick={() => setShowCompleted((v) => !v)}
            className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
          >
            {showCompleted
              ? `Hide completed (${completed.length})`
              : `Show completed (${completed.length})`}
          </button>

          {showCompleted && (
            <div className="space-y-2 mt-3">{completed.map(renderTodo)}</div>
          )}
        </div>
      )}
    </div>
  );
}
