import { useState } from 'react';
import { TodoItem, TodoPayload } from '../../hooks/api';
import { formatCompletedAt, formatDue, repeatLabel } from '../../lib/todos';

interface TodoRowProps {
  todo: TodoItem;
  selected: boolean;
  ringed: boolean;
  onSelect: () => void;
  onUpdate: (data: TodoPayload) => void;
  onDelete: () => void;
}

export function TodoRow({
  todo,
  selected,
  ringed,
  onSelect,
  onUpdate,
  onDelete,
}: TodoRowProps) {
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState('');

  const startEdit = () => {
    setEditing(true);
    setEditTitle(todo.title);
  };

  const saveEdit = () => {
    const trimmed = editTitle.trim();
    if (trimmed && trimmed !== todo.title) onUpdate({ title: trimmed });
    setEditing(false);
  };

  const due = formatDue(todo.due);
  const repeat = repeatLabel(todo.repeatInterval, todo.repeatUnit);

  return (
    <div
      id={`todo-row-${todo.id}`}
      onClick={onSelect}
      className={`flex items-start gap-3 p-3 rounded-lg border transition-colors ${
        todo.done
          ? 'border-white/5 bg-white/3 opacity-60'
          : 'border-white/10 bg-[var(--color-surface)]'
      } ${ringed ? 'ring-1 ring-[var(--color-primary)]' : ''}`}
    >
      <button
        onClick={e => {
          e.stopPropagation();
          onUpdate({ done: !todo.done });
        }}
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
            onClick={e => e.stopPropagation()}
            className="w-full bg-transparent text-[var(--color-text)] text-sm outline-none border-b border-[var(--color-primary)]"
          />
        ) : (
          <span
            onClick={e => {
              e.stopPropagation();
              startEdit();
            }}
            className={`text-sm cursor-text select-none ${
              todo.done
                ? 'line-through text-[var(--color-text-muted)]'
                : 'text-[var(--color-text)]'
            }`}
          >
            {todo.title}
          </span>
        )}
        {todo.notes && (
          <div
            className={`text-xs text-[var(--color-text-muted)] mt-0.5 ${
              selected ? 'whitespace-pre-wrap' : 'truncate'
            }`}
          >
            {todo.notes}
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 shrink-0">
        {due && (
          <span
            className={`text-xs ${
              due.overdue && !todo.done
                ? 'text-red-400'
                : 'text-[var(--color-text-muted)]'
            }`}
          >
            {due.label}
          </span>
        )}
        {repeat && (
          <span className="text-xs text-[var(--color-text-muted)]">
            ↻ {repeat}
          </span>
        )}
        {todo.done && todo.completedAt && (
          <span className="text-xs text-[var(--color-text-muted)]">
            {formatCompletedAt(todo.completedAt)}
          </span>
        )}
        <button
          onClick={e => {
            e.stopPropagation();
            onDelete();
          }}
          className="p-1 rounded text-[var(--color-text-muted)] hover:text-red-400 hover:bg-white/10 transition-colors text-xs"
          title="Delete"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
