import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, RepeatUnit, TodoList } from '../../hooks/api';
import { dueInputToUnix } from '../../lib/todos';

interface TodoFormProps {
  list: Exclude<TodoList, 'archive'>;
  onCancel: () => void;
}

// Four-step creation: title → notes → due date → repeat (number + unit).
// Tab cycles the fields, Ctrl+Enter creates from anywhere, Escape backs out
// into item navigation. The form stays open after a create for rapid entry.
export function TodoForm({ list, onCancel }: TodoFormProps) {
  const [title, setTitle] = useState('');
  const [notes, setNotes] = useState('');
  const [dueInput, setDueInput] = useState('');
  const [repeatN, setRepeatN] = useState('');
  const [repeatUnit, setRepeatUnit] = useState<RepeatUnit>('week');
  const refs = useRef<(HTMLInputElement | HTMLSelectElement | null)[]>([]);
  const queryClient = useQueryClient();

  const createTodo = useMutation({
    mutationFn: api.todos.create,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['todos'] });
      setTitle('');
      setNotes('');
      setDueInput('');
      setRepeatN('');
      refs.current[0]?.focus();
    },
  });

  const submit = () => {
    const trimmed = title.trim();
    if (!trimmed || createTodo.isPending) return;
    const interval = repeatN ? Number(repeatN) : null;
    createTodo.mutate({
      title: trimmed,
      list,
      notes: notes.trim() || undefined,
      due: dueInputToUnix(dueInput),
      repeatInterval: interval && interval >= 1 ? interval : undefined,
      repeatUnit: interval && interval >= 1 ? repeatUnit : undefined,
    });
  };

  const handleKey = (e: React.KeyboardEvent, idx: number) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      const n = refs.current.length;
      refs.current[(idx + (e.shiftKey ? n - 1 : 1)) % n]?.focus();
    } else if (e.key === 'Enter' && (e.ctrlKey || idx === 0)) {
      e.preventDefault();
      submit();
    } else if (e.key === 'Escape') {
      onCancel();
    }
  };

  const fieldClass =
    'bg-[var(--color-surface)] border border-white/20 rounded px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] outline-none focus:border-[var(--color-primary)]';

  return (
    <div className="mb-3 p-3 rounded-lg border border-[var(--color-primary)]/40 bg-[var(--color-surface)] space-y-2">
      <input
        ref={el => {
          refs.current[0] = el;
        }}
        autoFocus
        value={title}
        onChange={e => setTitle(e.target.value)}
        onKeyDown={e => handleKey(e, 0)}
        placeholder="Title…"
        className={`${fieldClass} w-full`}
      />
      <input
        ref={el => {
          refs.current[1] = el;
        }}
        value={notes}
        onChange={e => setNotes(e.target.value)}
        onKeyDown={e => handleKey(e, 1)}
        placeholder="More information…"
        className={`${fieldClass} w-full text-xs`}
      />
      <div className="flex gap-2 items-center">
        <label className="text-xs text-[var(--color-text-muted)]">Due</label>
        <input
          ref={el => {
            refs.current[2] = el;
          }}
          type="date"
          value={dueInput}
          onChange={e => setDueInput(e.target.value)}
          onKeyDown={e => handleKey(e, 2)}
          className={fieldClass}
        />
        <label className="text-xs text-[var(--color-text-muted)] ml-2">
          Every
        </label>
        <input
          ref={el => {
            refs.current[3] = el;
          }}
          type="number"
          min={1}
          value={repeatN}
          onChange={e => setRepeatN(e.target.value)}
          onKeyDown={e => handleKey(e, 3)}
          placeholder="—"
          className={`${fieldClass} w-16`}
        />
        <select
          ref={el => {
            refs.current[4] = el;
          }}
          value={repeatUnit}
          onChange={e => setRepeatUnit(e.target.value as RepeatUnit)}
          onKeyDown={e => handleKey(e, 4)}
          className={fieldClass}
        >
          <option value="day">days</option>
          <option value="week">weeks</option>
          <option value="month">months</option>
        </select>
      </div>
      <div className="flex gap-2 justify-end">
        <button
          onClick={onCancel}
          className="px-3 py-1.5 rounded text-[var(--color-text-muted)] hover:bg-white/10 transition-colors text-sm"
        >
          Cancel
        </button>
        <button
          onClick={submit}
          disabled={!title.trim() || createTodo.isPending}
          className="px-3 py-1.5 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-40 transition-colors text-sm"
        >
          Add
        </button>
      </div>
    </div>
  );
}
