import { api, TodoItem, TodoList, TodoPayload } from '../../hooks/api';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { RecordingButton } from '../RecordingButton';
import { useRecorder } from '@/hooks/useRecorder';
import { ulid } from '@/lib/ulid';
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

  // Voice-to-do: record, transcribe, and send the words to the backend, which
  // runs them through the LLM and creates the to-do in one shot. The id is
  // minted here so a retried request cannot make a second to-do.
  const createFromSpeech = useMutation({
    mutationFn: ({ text, id }: { text: string; id: string }) =>
      api.todos.transcribe(text, id),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['todos'] });
    },
  });
  const transcribeTodo = useRecorder(text => {
    if (text.trim()) createFromSpeech.mutate({ text, id: ulid() });
  });
  const voiceError =
    transcribeTodo.error ||
    (createFromSpeech.error
      ? `Couldn't create the to-do: ${createFromSpeech.error.message}`
      : '');

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
          <div className="flex items-center gap-2 mb-3">
            <button
              onClick={onStartCreate}
              className="flex-1 px-3 py-2 rounded-lg border border-dashed border-white/15 text-left text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-white/30 transition-colors"
            >
              + Add to-do…
            </button>
            <RecordingButton
              status={
                createFromSpeech.isPending ? 'saving' : transcribeTodo.status
              }
              starting={transcribeTodo.starting}
              action="dictate"
              title="Say a to-do — it is added straight to the list"
              testId="todo-dictate"
              onClick={() => {
                if (transcribeTodo.status === 'idle') {
                  createFromSpeech.reset();
                  void transcribeTodo.start();
                } else transcribeTodo.stop();
              }}
              className="shrink-0"
            />
          </div>
        ))}
      {activeList !== 'archive' && !creating && voiceError && (
        <p role="alert" className="-mt-2 mb-3 text-xs text-red-400">
          {voiceError}
        </p>
      )}

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
