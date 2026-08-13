import { api, TodoItem, TodoList, TodoPayload } from '../../hooks/api';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { TodoForm } from './TodoForm';
import { TodoRow } from './TodoRow';
import type { TaskSection } from './index';

// Chores used to be a third list here. It was the same rows either way, and
// once Lifestyle showed to-dos directly there was nothing left for the split to
// mean — the rows were folded into 'todo' (backend/db/connection.py).
const LISTS: { id: TodoList; label: string }[] = [
  { id: 'todo', label: 'To-Do' },
  { id: 'archive', label: 'Archive' },
];

interface TodoSectionProps {
  activeList: TodoList;
  section: TaskSection;
  level: number;
  counts: Record<TodoList, number>;
  active: TodoItem[];
  isLoading: boolean;
  selectedId: string | null;
  creating: boolean;
  /** Set by the card-level toggle in the DailyTasks header, above. */
  showDelete: boolean;
  onSelectList: (list: TodoList) => void;
  onSelectTodo: (id: string) => void;
  onStartCreate: () => void;
  onCancelCreate: () => void;
  onUpdateTodo: (id: string, data: TodoPayload) => void;
}

export function TodoSection({
  activeList,
  section,
  level,
  counts,
  active,
  isLoading,
  selectedId,
  creating,
  showDelete,
  onSelectList,
  onSelectTodo,
  onStartCreate,
  onCancelCreate,
  onUpdateTodo,
}: TodoSectionProps) {
  const queryClient = useQueryClient();
  const deleteTodo = useMutation({
    mutationFn: (id: string) => api.todos.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['todos'] });
      // Deleting an active todo logs a "removed" notification in the Journal.
      queryClient.invalidateQueries({ queryKey: ['taskEvents'] });
    },
  });

  const pillClass = (list: TodoList) => {
    const isActive = list === activeList;
    const focused = level === 1 && section === list;
    return `px-3 py-1 text-sm rounded-full border transition-colors ${
      isActive
        ? 'bg-[var(--color-primary)] border-[var(--color-primary)] text-white'
        : 'border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
    }${focused ? ' ring-1 ring-white/70' : ''}`;
  };

  const renderTodo = (todo: TodoItem) => (
    <TodoRow
      key={todo.id}
      todo={todo}
      selected={selectedId === todo.id}
      ringed={level >= 2 && selectedId === todo.id}
      showDelete={showDelete}
      onSelect={() => onSelectTodo(todo.id)}
      onUpdate={data => onUpdateTodo(todo.id, data)}
      onDelete={() => deleteTodo.mutate(todo.id)}
    />
  );

  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 mb-3">
        <h2 className="text-lg font-semibold text-[var(--color-text)]">
          To-Do
        </h2>
        <div className="flex items-center gap-2">
          {LISTS.map(({ id, label }) => (
            <button
              key={id}
              onClick={() => onSelectList(id)}
              className={pillClass(id)}
            >
              {label}
              {counts[id] > 0 && (
                <span className="opacity-60 ml-1">{counts[id]}</span>
              )}
            </button>
          ))}
        </div>
      </div>

      {isLoading && (
        <div className="text-[var(--color-text-muted)] text-sm">Loading…</div>
      )}

      {/* Creation comes first: drill-in lands here with the title focused. */}
      {activeList !== 'archive' &&
        (creating ? (
          <TodoForm list={activeList} onCancel={onCancelCreate} />
        ) : (
          <button
            onClick={onStartCreate}
            className="w-full mb-3 px-3 py-2 rounded-lg border border-dashed border-white/15 text-left text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-white/30 transition-colors"
          >
            + Add to-do…
          </button>
        ))}

      <div className="space-y-2">
        {active.map(renderTodo)}

        {active.length === 0 && !isLoading && (
          <div className="text-center py-6 text-[var(--color-text-muted)] text-sm">
            Nothing on the list.
          </div>
        )}
      </div>
    </div>
  );
}
