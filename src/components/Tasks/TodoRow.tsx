import { useDraftState } from '@/hooks/useDraftState';
import { TodoItem, TodoPayload } from '../../hooks/api';
import {
  formatCompletedAt,
  formatDue,
  priorityFlag,
  repeatLabel,
} from '../../lib/todos';
import { InlineEditableText } from '../InlineEditableText';

interface TodoRowProps {
  todo: TodoItem;
  selected: boolean;
  ringed: boolean;
  /** Delete is behind a toggle in the section header, the way the workout log
   *  and the journal hide theirs — an ✕ on every row, one tap from destroying
   *  an item, sat directly beside the row's own click targets. */
  showDelete: boolean;
  onSelect: () => void;
  onUpdate: (data: TodoPayload) => void;
  onDelete: () => void;
}

export function TodoRow({
  todo,
  selected,
  ringed,
  showDelete,
  onSelect,
  onUpdate,
  onDelete,
}: TodoRowProps) {
  const [editing, setEditing] = useDraftState(`todo:${todo.id}:editing`, false);

  const saveEdit = (title: string) => {
    if (title !== todo.title) onUpdate({ title });
    setEditing(false);
  };

  const due = formatDue(todo.due);
  const repeat = repeatLabel(todo.repeatInterval, todo.repeatUnit);
  const flag = priorityFlag(todo.priority);

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
        <InlineEditableText
          value={todo.title}
          editing={editing}
          onStartEdit={() => setEditing(true)}
          onStopEdit={() => setEditing(false)}
          onSave={saveEdit}
          draftKey={`todo:${todo.id}:edit-title`}
          stopClickPropagation
          displayClassName={
            todo.done
              ? 'line-through text-[var(--color-text-muted)]'
              : 'text-[var(--color-text)]'
          }
        />
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
        {flag && !todo.done && (
          <span
            className={`text-xs font-medium ${flag.className}`}
            title={flag.title}
          >
            ⚑{flag.label}
          </span>
        )}
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
        {showDelete && (
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
        )}
      </div>
    </div>
  );
}
