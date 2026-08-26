import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  api,
  type ChatTodoItem,
  type ChatTodoPayload,
  type RepeatUnit,
  type TodoList,
  type TodoPayload,
} from '../../hooks/api';
import {
  PRIORITY_LABELS,
  dueInputToUnix,
  dueIsoToInput,
} from '../../lib/todos';

const fieldClass =
  'bg-[var(--color-surface)] border border-white/20 rounded px-2 py-1 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] outline-none focus:border-[var(--color-primary)]';

interface RowProps {
  todo: ChatTodoItem;
  promoting: boolean;
  onToggleDone: () => void;
  onUpdateTitle: (title: string) => void;
  onDismiss: () => void;
  onPromote: (data: TodoPayload & { title: string }) => void;
}

/** One row in the bar. Two independent interactions on the same title text,
 * switched by the "→ Permanent" toggle: off, clicking the title is a plain
 * inline rename (TodoRow.tsx's own pattern); on, clicking it expands the row
 * into a full TodoForm-parity editor that ends in promoting the item instead
 * of just retitling it. */
function ChatTodoRow({
  todo,
  promoting,
  onToggleDone,
  onUpdateTitle,
  onDismiss,
  onPromote,
}: RowProps) {
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState('');
  const [sendMode, setSendMode] = useState(false);
  const [open, setOpen] = useState(false);

  const [title, setTitle] = useState(todo.title);
  const [notes, setNotes] = useState(todo.notes ?? '');
  const [dueInput, setDueInput] = useState('');
  const [priority, setPriority] = useState(3);
  const [list, setList] = useState<TodoList>('todo');
  const [repeatN, setRepeatN] = useState('');
  const [repeatUnit, setRepeatUnit] = useState<RepeatUnit>('week');

  const startEdit = () => {
    setEditing(true);
    setEditTitle(todo.title);
  };
  const saveEdit = () => {
    const trimmed = editTitle.trim();
    if (trimmed && trimmed !== todo.title) onUpdateTitle(trimmed);
    setEditing(false);
  };

  const openPromote = () => {
    setTitle(todo.title);
    setNotes(todo.notes ?? '');
    setDueInput(dueIsoToInput(todo.due));
    setPriority(todo.priority);
    setList('todo');
    setRepeatN('');
    setOpen(true);
  };

  const submitPromote = () => {
    const trimmed = title.trim();
    if (!trimmed || promoting) return;
    const interval = repeatN ? Number(repeatN) : null;
    onPromote({
      title: trimmed,
      notes: notes.trim() || undefined,
      due: dueInputToUnix(dueInput),
      priority,
      list,
      repeatInterval: interval && interval >= 1 ? interval : undefined,
      repeatUnit: interval && interval >= 1 ? repeatUnit : undefined,
    });
  };

  if (open) {
    return (
      <div className="p-3 rounded-lg border border-[var(--color-primary)]/40 bg-[var(--color-surface)] space-y-2">
        <input
          autoFocus
          value={title}
          onChange={e => setTitle(e.target.value)}
          placeholder="Title…"
          className={`${fieldClass} w-full`}
        />
        <input
          value={notes}
          onChange={e => setNotes(e.target.value)}
          placeholder="More information…"
          className={`${fieldClass} w-full text-xs`}
        />
        <div className="flex flex-wrap gap-2 items-center">
          <label className="text-xs text-[var(--color-text-muted)]">Due</label>
          <input
            aria-label="Due date"
            type="date"
            value={dueInput}
            onChange={e => setDueInput(e.target.value)}
            className={fieldClass}
          />
          <label className="text-xs text-[var(--color-text-muted)] ml-2">
            List
          </label>
          <select
            value={list}
            onChange={e => setList(e.target.value as TodoList)}
            className={fieldClass}
          >
            <option value="todo">To-do</option>
            <option value="archive">Archive</option>
          </select>
          <label className="text-xs text-[var(--color-text-muted)] ml-2">
            Every
          </label>
          <input
            type="number"
            min={1}
            value={repeatN}
            onChange={e => setRepeatN(e.target.value)}
            placeholder="—"
            className={`${fieldClass} w-16`}
          />
          <select
            value={repeatUnit}
            onChange={e => setRepeatUnit(e.target.value as RepeatUnit)}
            className={fieldClass}
          >
            <option value="day">days</option>
            <option value="week">weeks</option>
            <option value="month">months</option>
          </select>
          <label className="text-xs text-[var(--color-text-muted)] ml-2">
            Priority
          </label>
          <select
            value={priority}
            onChange={e => setPriority(Number(e.target.value))}
            className={fieldClass}
          >
            {Object.entries(PRIORITY_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {value} — {label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex gap-2 justify-end">
          <button
            onClick={() => setOpen(false)}
            className="px-3 py-1.5 rounded text-[var(--color-text-muted)] hover:bg-white/10 transition-colors text-sm"
          >
            Cancel
          </button>
          <button
            onClick={submitPromote}
            disabled={!title.trim() || promoting}
            className="px-3 py-1.5 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-40 transition-colors text-sm"
          >
            {promoting ? 'Saving…' : 'Save and send to permanent'}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      className={`flex items-center gap-2 px-2 py-1.5 rounded-lg border transition-colors ${
        todo.done
          ? 'border-white/5 bg-white/3 opacity-60'
          : 'border-white/10 bg-[var(--color-surface)]'
      }`}
    >
      <button
        onClick={onToggleDone}
        className={`w-5 h-5 rounded border shrink-0 flex items-center justify-center transition-colors ${
          todo.done
            ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
            : 'border-white/30 hover:border-white/50'
        }`}
      >
        {todo.done && <span className="text-xs">✓</span>}
      </button>

      <div className="flex-1 min-w-0">
        {editing ? (
          <input
            autoFocus
            value={editTitle}
            onChange={e => setEditTitle(e.target.value)}
            onBlur={saveEdit}
            onKeyDown={e => {
              if (e.key === 'Enter') saveEdit();
              if (e.key === 'Escape') setEditing(false);
            }}
            className="w-full bg-transparent text-[var(--color-text)] text-sm outline-none border-b border-[var(--color-primary)]"
          />
        ) : (
          <span
            onClick={() => (sendMode ? openPromote() : startEdit())}
            className={`text-sm cursor-text select-none ${
              todo.done
                ? 'line-through text-[var(--color-text-muted)]'
                : 'text-[var(--color-text)]'
            }`}
          >
            {todo.title}
          </span>
        )}
        {todo.notes && !editing && (
          <div className="text-xs text-[var(--color-text-muted)] mt-0.5 truncate">
            {todo.notes}
          </div>
        )}
      </div>

      <button
        onClick={() => setSendMode(s => !s)}
        title={
          sendMode
            ? 'Click the to-do to send it to your permanent list'
            : 'Switch to send-to-permanent mode'
        }
        className={`shrink-0 text-[10px] uppercase tracking-wide rounded px-1.5 py-0.5 border transition-colors ${
          sendMode
            ? 'border-[var(--color-primary)] text-[var(--color-primary)]'
            : 'border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
        }`}
      >
        → Permanent
      </button>

      <button
        onClick={onDismiss}
        className="shrink-0 p-1 rounded text-[var(--color-text-muted)] hover:text-red-400 hover:bg-white/10 transition-colors text-xs"
        title="Dismiss"
      >
        ✕
      </button>
    </div>
  );
}

/** Collapsible bar docked above the chat input, replacing the old inline
 * briefing accept/reject card and the delegate's staged to-do proposal. Both
 * the morning briefing and the chat delegate's add_todos tool write straight
 * into `chat_todos` with no confirm step; this bar is where the result gets
 * edited, completed, dismissed, or promoted to the permanent to-do list
 * afterward. Always collapsed on load — a bar that pops itself open would
 * compete with the note-cards panel for attention. */
export function ChatTodoBar() {
  const [expanded, setExpanded] = useState(false);
  const queryClient = useQueryClient();

  const { data: todos = [] } = useQuery({
    queryKey: ['chatTodos'],
    queryFn: api.chatTodos.list,
  });

  const invalidateTodos = () =>
    queryClient.invalidateQueries({ queryKey: ['chatTodos'] });

  const update = useMutation({
    mutationFn: ({ id, data }: { id: string; data: ChatTodoPayload }) =>
      api.chatTodos.update(id, data),
    onSuccess: () => {
      invalidateTodos();
      // A completion logs a task_events row (backend/routes/tasks.py's
      // _complete_chat_todo_row) the Journal feed reads from that key.
      queryClient.invalidateQueries({ queryKey: ['taskEvents'] });
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.chatTodos.remove(id),
    onSuccess: invalidateTodos,
  });

  const promote = useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: string;
      data: TodoPayload & { title: string };
    }) => api.chatTodos.promote(id, data),
    onSuccess: () => {
      invalidateTodos();
      queryClient.invalidateQueries({ queryKey: ['todos'] });
    },
  });

  const pending = todos.filter(t => !t.done).length;

  return (
    <div className="border-t border-white/10">
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center justify-between gap-2 px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
      >
        <span>
          {todos.length === 0
            ? "Today's to-dos"
            : `${pending} to-do${pending === 1 ? '' : 's'} today`}
        </span>
        <span
          className={`transition-transform ${expanded ? 'rotate-180' : ''}`}
        >
          ▾
        </span>
      </button>
      {expanded && (
        <div className="px-4 pb-3 space-y-2 max-h-64 overflow-y-auto">
          {todos.length === 0 ? (
            <div className="text-xs text-[var(--color-text-muted)] py-1">
              Nothing added yet today.
            </div>
          ) : (
            todos.map(todo => (
              <ChatTodoRow
                key={todo.id}
                todo={todo}
                promoting={
                  promote.isPending && promote.variables?.id === todo.id
                }
                onToggleDone={() =>
                  update.mutate({ id: todo.id, data: { done: !todo.done } })
                }
                onUpdateTitle={title =>
                  update.mutate({ id: todo.id, data: { title } })
                }
                onDismiss={() => remove.mutate(todo.id)}
                onPromote={data => promote.mutate({ id: todo.id, data })}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}
