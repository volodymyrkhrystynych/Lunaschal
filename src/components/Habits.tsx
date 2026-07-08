import { useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, Habit, HabitCheck, HabitCheckStatus, HabitInput } from '../hooks/api';
import { cellLevel, gridWeeks, isScheduled, nextBooleanState, toISODate } from '../lib/habits';

const GRID_WEEKS = 20;
const COLORS = ['#22c55e', '#3b82f6', '#f59e0b', '#ef4444', '#a855f7', '#ec4899'];
const WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const LEVEL_OPACITY = [0, 0.3, 0.5, 0.75, 1];

type CheckMap = Map<string, HabitCheck>;

export function Habits() {
  const [showAdd, setShowAdd] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const queryClient = useQueryClient();

  const todayISO = toISODate(new Date());
  const weeks = useMemo(() => gridWeeks(todayISO, GRID_WEEKS), [todayISO]);
  const gridFrom = weeks[0][0];

  const { data: habits = [], isLoading } = useQuery({
    queryKey: ['habits'],
    queryFn: () => api.habits.list(true),
  });

  const { data: checks = [] } = useQuery({
    queryKey: ['habitChecks', gridFrom, todayISO],
    queryFn: () => api.habits.checks(gridFrom, todayISO),
  });

  const checksByHabit = useMemo(() => {
    const map = new Map<string, CheckMap>();
    for (const c of checks) {
      if (!map.has(c.habitId)) map.set(c.habitId, new Map());
      map.get(c.habitId)!.set(c.date, c);
    }
    return map;
  }, [checks]);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['habits'] });
    queryClient.invalidateQueries({ queryKey: ['habitChecks'] });
  };

  const createHabit = useMutation({
    mutationFn: (data: HabitInput) => api.habits.create(data),
    onSuccess: () => { invalidate(); setShowAdd(false); },
  });

  const updateHabit = useMutation({
    mutationFn: ({ id, data }: { id: string; data: HabitInput }) => api.habits.update(id, data),
    onSuccess: () => { invalidate(); setEditingId(null); },
  });

  const deleteHabit = useMutation({
    mutationFn: (id: string) => api.habits.remove(id),
    onSuccess: invalidate,
  });

  const reorderHabits = useMutation({
    mutationFn: (order: string[]) => api.habits.reorder(order),
    onSuccess: invalidate,
  });

  const setCheck = useMutation({
    mutationFn: ({ id, date, status, value }: { id: string; date: string; status: HabitCheckStatus; value?: number }) =>
      api.habits.setCheck(id, date, { status, value }),
    onSuccess: invalidate,
  });

  const active = habits.filter((h) => !h.archived).sort((a, b) => a.position - b.position);
  const archived = habits.filter((h) => h.archived);
  const scheduledToday = active.filter((h) => h.todayScheduled);
  const offToday = active.filter((h) => !h.todayScheduled);

  const moveHabit = (index: number, direction: -1 | 1) => {
    const swapped = [...active];
    const target = index + direction;
    if (target < 0 || target >= swapped.length) return;
    [swapped[index], swapped[target]] = [swapped[target], swapped[index]];
    reorderHabits.mutate(swapped.map((h) => h.id));
  };

  const renderRow = (habit: Habit, index: number, dimmed: boolean) => (
    <HabitRow
      key={habit.id}
      habit={habit}
      checks={checksByHabit.get(habit.id) ?? new Map()}
      weeks={weeks}
      todayISO={todayISO}
      dimmed={dimmed}
      expanded={expandedId === habit.id}
      onToggleExpand={() => setExpandedId(expandedId === habit.id ? null : habit.id)}
      editing={editingId === habit.id}
      onStartEdit={() => { setEditingId(habit.id); setShowAdd(false); }}
      onSaveEdit={(data) => updateHabit.mutate({ id: habit.id, data })}
      onCancelEdit={() => setEditingId(null)}
      onArchive={() => updateHabit.mutate({ id: habit.id, data: { archived: true } })}
      onMove={dimmed ? undefined : (dir) => moveHabit(index, dir)}
      canMoveUp={index > 0}
      canMoveDown={index < active.length - 1}
      onSetCheck={(date, status, value) => setCheck.mutate({ id: habit.id, date, status, value })}
      saveError={editingId === habit.id ? updateHabit.error?.message : undefined}
    />
  );

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-2xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-xl font-semibold text-[var(--color-text)]">Habits</h2>
          {!showAdd && (
            <button
              onClick={() => { setShowAdd(true); setEditingId(null); }}
              className="text-sm px-3 py-1.5 rounded border border-white/20 text-[var(--color-text)] hover:bg-white/10 transition-colors"
            >
              + Add habit
            </button>
          )}
        </div>

        {isLoading && <div className="text-[var(--color-text-muted)] text-sm">Loading…</div>}

        {showAdd && (
          <div className="mb-4">
            <HabitForm
              onSubmit={(data) => createHabit.mutate(data)}
              onCancel={() => setShowAdd(false)}
              pending={createHabit.isPending}
              error={createHabit.error?.message}
            />
          </div>
        )}

        <div className="space-y-2">
          {scheduledToday.map((habit) => renderRow(habit, active.indexOf(habit), false))}

          {active.length === 0 && !showAdd && !isLoading && (
            <div className="text-center py-12 text-[var(--color-text-muted)] text-sm">
              No habits yet. Add one to start a streak.
            </div>
          )}
        </div>

        {offToday.length > 0 && (
          <div className="mt-6">
            <div className="text-xs text-[var(--color-text-muted)] mb-2">Not scheduled today</div>
            <div className="space-y-2 opacity-70">
              {offToday.map((habit) => renderRow(habit, active.indexOf(habit), true))}
            </div>
          </div>
        )}

        {archived.length > 0 && (
          <div className="mt-10">
            <button
              onClick={() => setShowArchived(!showArchived)}
              className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
            >
              {showArchived ? '▾' : '▸'} Archived ({archived.length})
            </button>
            {showArchived && (
              <div className="mt-2 space-y-2">
                {archived.map((habit) => (
                  <div
                    key={habit.id}
                    className="flex items-center gap-3 p-3 rounded-lg border border-white/5 bg-white/3 opacity-60"
                  >
                    <span className="flex-1 text-sm text-[var(--color-text-muted)]">{habit.name}</span>
                    <span className="text-xs text-[var(--color-text-muted)]">
                      best {habit.longestStreak}{habit.streakUnit === 'days' ? 'd' : 'w'}
                    </span>
                    <button
                      onClick={() => updateHabit.mutate({ id: habit.id, data: { archived: false } })}
                      className="text-xs px-2 py-1 rounded border border-white/20 text-[var(--color-text)] hover:bg-white/10 transition-colors"
                    >
                      Unarchive
                    </button>
                    <button
                      onClick={() => {
                        if (window.confirm(`Delete "${habit.name}" and its history?`)) deleteHabit.mutate(habit.id);
                      }}
                      className="p-1 rounded text-[var(--color-text-muted)] hover:text-red-400 hover:bg-white/10 transition-colors text-xs"
                      title="Delete permanently"
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function streakBadge(habit: Habit) {
  const unit = habit.streakUnit === 'days' ? 'd' : 'w';
  return `🔥 ${habit.currentStreak}${unit}`;
}

interface HabitRowProps {
  habit: Habit;
  checks: CheckMap;
  weeks: string[][];
  todayISO: string;
  dimmed: boolean;
  expanded: boolean;
  onToggleExpand: () => void;
  editing: boolean;
  onStartEdit: () => void;
  onSaveEdit: (data: HabitInput) => void;
  onCancelEdit: () => void;
  onArchive: () => void;
  onMove?: (direction: -1 | 1) => void;
  canMoveUp: boolean;
  canMoveDown: boolean;
  onSetCheck: (date: string, status: HabitCheckStatus, value?: number) => void;
  saveError?: string;
}

function HabitRow(props: HabitRowProps) {
  const { habit, checks, weeks, todayISO, expanded, editing } = props;

  if (editing) {
    return (
      <HabitForm
        initial={habit}
        onSubmit={props.onSaveEdit}
        onCancel={props.onCancelEdit}
        error={props.saveError}
      />
    );
  }

  const color = habit.color || 'var(--color-primary)';

  return (
    <div className="rounded-lg border border-white/10 bg-[var(--color-surface)]">
      <div className="flex items-center gap-3 p-3">
        <TodayControl habit={habit} todayISO={todayISO} onSetCheck={props.onSetCheck} />

        <button onClick={props.onToggleExpand} className="flex-1 min-w-0 text-left">
          <div className="flex items-baseline gap-2">
            <span className={`text-sm truncate ${habit.todaySatisfied ? 'text-[var(--color-text-muted)]' : 'text-[var(--color-text)]'}`}>
              {habit.name}
            </span>
            <span className="text-xs text-[var(--color-text-muted)] shrink-0">
              {habit.type === 'quantity' && habit.targetValue != null && (
                <>{habit.todayValue ?? 0}/{habit.targetValue}{habit.unit ? ` ${habit.unit}` : ''} · </>
              )}
              {scheduleLabel(habit)}
            </span>
          </div>
        </button>

        <div className="flex items-center gap-2 shrink-0 text-xs">
          {habit.currentStreak > 0 && (
            <span className="text-[var(--color-text)]" title={`Longest: ${habit.longestStreak}`}>
              {streakBadge(habit)}
            </span>
          )}
          {habit.completion30 != null && (
            <span className="text-[var(--color-text-muted)]" title="Completion over the last 30 days">
              {habit.completion30}%
            </span>
          )}
          <button
            onClick={props.onToggleExpand}
            className="p-1 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/10 transition-colors"
            title={expanded ? 'Hide history' : 'Show history'}
          >
            {expanded ? '▾' : '▸'}
          </button>
        </div>
      </div>

      {expanded && (
        <div className="px-3 pb-3 border-t border-white/5 pt-3">
          <Heatmap
            habit={habit}
            checks={checks}
            weeks={weeks}
            todayISO={todayISO}
            color={color}
            onSetCheck={props.onSetCheck}
          />
          <div className="flex items-center gap-1 mt-3 text-xs">
            <span className="text-[var(--color-text-muted)] mr-auto">
              Longest streak: {habit.longestStreak}{habit.streakUnit === 'days' ? ' days' : ' weeks'}
            </span>
            {props.onMove && (
              <>
                <button onClick={() => props.onMove!(-1)} disabled={!props.canMoveUp}
                  className="p-1 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/10 disabled:opacity-20 transition-colors" title="Move up">↑</button>
                <button onClick={() => props.onMove!(1)} disabled={!props.canMoveDown}
                  className="p-1 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/10 disabled:opacity-20 transition-colors" title="Move down">↓</button>
              </>
            )}
            <button onClick={props.onStartEdit}
              className="px-2 py-1 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/10 transition-colors" title="Edit">Edit</button>
            <button onClick={props.onArchive}
              className="px-2 py-1 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/10 transition-colors" title="Archive">Archive</button>
          </div>
        </div>
      )}
    </div>
  );
}

function scheduleLabel(habit: Habit): string {
  if (habit.scheduleType === 'weekdays') {
    return (habit.scheduleDays ?? []).map((d) => WEEKDAY_LABELS[d]).join('/');
  }
  if (habit.scheduleType === 'per_week') return `${habit.timesPerWeek}×/week`;
  return 'daily';
}

function TodayControl({ habit, todayISO, onSetCheck }: {
  habit: Habit;
  todayISO: string;
  onSetCheck: (date: string, status: HabitCheckStatus, value?: number) => void;
}) {
  if (habit.type === 'boolean') {
    const status = habit.todayStatus;
    return (
      <button
        onClick={() => onSetCheck(todayISO, nextBooleanState(status))}
        title="Click to cycle: done → skipped → clear"
        className={`w-6 h-6 rounded border shrink-0 flex items-center justify-center text-xs transition-colors ${
          status === 'done'
            ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
            : status === 'skipped'
              ? 'border-white/30 border-dashed text-[var(--color-text-muted)]'
              : 'border-white/30 hover:border-white/50'
        }`}
      >
        {status === 'done' ? '✓' : status === 'skipped' ? '»' : ''}
      </button>
    );
  }

  const value = habit.todayValue ?? 0;
  const skipped = habit.todayStatus === 'skipped';
  return (
    <div className="flex items-center gap-1 shrink-0">
      <button
        onClick={() => onSetCheck(todayISO, 'done', Math.max(0, value - 1))}
        disabled={skipped || value <= 0}
        className="w-6 h-6 rounded border border-white/30 text-[var(--color-text)] hover:bg-white/10 disabled:opacity-30 transition-colors text-xs"
      >
        −
      </button>
      <button
        onClick={() => onSetCheck(todayISO, 'done', value + 1)}
        disabled={skipped}
        className={`w-6 h-6 rounded border transition-colors text-xs ${
          habit.todaySatisfied
            ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
            : 'border-white/30 text-[var(--color-text)] hover:bg-white/10'
        }`}
      >
        +
      </button>
      <button
        onClick={() => onSetCheck(todayISO, skipped ? 'none' : 'skipped')}
        title={skipped ? 'Unskip today' : 'Skip today (keeps the streak)'}
        className={`w-6 h-6 rounded border text-xs transition-colors ${
          skipped
            ? 'border-white/30 border-dashed text-[var(--color-text-muted)] bg-white/5'
            : 'border-white/20 text-[var(--color-text-muted)] hover:bg-white/10'
        }`}
      >
        »
      </button>
    </div>
  );
}

function Heatmap({ habit, checks, weeks, todayISO, color, onSetCheck }: {
  habit: Habit;
  checks: CheckMap;
  weeks: string[][];
  todayISO: string;
  color: string;
  onSetCheck: (date: string, status: HabitCheckStatus, value?: number) => void;
}) {
  const [editingCell, setEditingCell] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');

  const monthLabels = weeks.map((col, i) => {
    const month = col[0].slice(5, 7);
    const prev = i > 0 ? weeks[i - 1][0].slice(5, 7) : null;
    if (month !== prev) {
      return new Date(col[0] + 'T00:00:00').toLocaleString(undefined, { month: 'short' });
    }
    return '';
  });

  const clickCell = (date: string) => {
    if (date > todayISO) return;
    if (habit.type === 'boolean') {
      const current = checks.get(date)?.status ?? 'none';
      onSetCheck(date, nextBooleanState(current));
    } else {
      setEditingCell(editingCell === date ? null : date);
      setEditValue(String(checks.get(date)?.value ?? ''));
    }
  };

  return (
    <div>
      <div className="overflow-x-auto">
        <div className="inline-block">
          <div className="flex gap-[3px] ml-8 mb-1">
            {monthLabels.map((label, i) => (
              <div key={i} className="w-3 text-[9px] text-[var(--color-text-muted)] overflow-visible whitespace-nowrap">
                {label}
              </div>
            ))}
          </div>
          <div className="flex gap-[3px]">
            <div className="flex flex-col gap-[3px] w-7 mr-1">
              {WEEKDAY_LABELS.map((label, d) => (
                <div key={d} className="h-3 text-[9px] leading-3 text-[var(--color-text-muted)]">
                  {d % 2 === 0 ? label : ''}
                </div>
              ))}
            </div>
            {weeks.map((col, w) => (
              <div key={w} className="flex flex-col gap-[3px]">
                {col.map((date) => {
                  const future = date > todayISO;
                  const check = checks.get(date);
                  const level = cellLevel(check, habit);
                  const scheduled = isScheduled(date, habit);
                  let style: React.CSSProperties = {};
                  let cls = 'w-3 h-3 rounded-[2px] transition-colors ';
                  if (future) {
                    cls += 'bg-transparent';
                  } else if (level === 'skipped') {
                    cls += 'border border-dashed border-white/40';
                  } else if (level === 0) {
                    cls += scheduled ? 'bg-white/10' : 'bg-white/4';
                  } else {
                    style = { backgroundColor: color, opacity: LEVEL_OPACITY[level] };
                  }
                  if (date === todayISO) cls += ' ring-1 ring-white/50';
                  return (
                    <button
                      key={date}
                      onClick={() => clickCell(date)}
                      disabled={future}
                      className={cls + (future ? '' : ' hover:ring-1 hover:ring-white/60 cursor-pointer')}
                      style={style}
                      title={`${date}${check ? ` — ${check.status}${check.value != null ? ` (${check.value})` : ''}` : ''}`}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      </div>

      {editingCell && (
        <div className="flex items-center gap-2 mt-3 text-xs">
          <span className="text-[var(--color-text-muted)]">{editingCell}:</span>
          <input
            autoFocus
            type="number"
            min="0"
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                onSetCheck(editingCell, 'done', Number(editValue) || 0);
                setEditingCell(null);
              }
              if (e.key === 'Escape') setEditingCell(null);
            }}
            className="w-20 bg-[var(--color-surface)] border border-white/20 rounded px-2 py-1 text-[var(--color-text)] outline-none focus:border-[var(--color-primary)]"
          />
          {habit.unit && <span className="text-[var(--color-text-muted)]">{habit.unit}</span>}
          <button
            onClick={() => { onSetCheck(editingCell, 'done', Number(editValue) || 0); setEditingCell(null); }}
            className="px-2 py-1 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 transition-colors"
          >
            Save
          </button>
          <button
            onClick={() => { onSetCheck(editingCell, 'skipped'); setEditingCell(null); }}
            className="px-2 py-1 rounded text-[var(--color-text-muted)] hover:bg-white/10 transition-colors"
          >
            Skip
          </button>
          <button
            onClick={() => { onSetCheck(editingCell, 'none'); setEditingCell(null); }}
            className="px-2 py-1 rounded text-[var(--color-text-muted)] hover:bg-white/10 transition-colors"
          >
            Clear
          </button>
          <button
            onClick={() => setEditingCell(null)}
            className="px-2 py-1 rounded text-[var(--color-text-muted)] hover:bg-white/10 transition-colors"
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}

function HabitForm({ initial, onSubmit, onCancel, pending, error }: {
  initial?: Habit;
  onSubmit: (data: HabitInput) => void;
  onCancel: () => void;
  pending?: boolean;
  error?: string;
}) {
  const [name, setName] = useState(initial?.name ?? '');
  const [isQuantity, setIsQuantity] = useState(initial?.type === 'quantity');
  const [targetValue, setTargetValue] = useState(initial?.targetValue != null ? String(initial.targetValue) : '');
  const [unit, setUnit] = useState(initial?.unit ?? '');
  const [scheduleType, setScheduleType] = useState(initial?.scheduleType ?? 'daily');
  const [scheduleDays, setScheduleDays] = useState<number[]>(initial?.scheduleDays ?? []);
  const [timesPerWeek, setTimesPerWeek] = useState(initial?.timesPerWeek != null ? String(initial.timesPerWeek) : '3');
  const [color, setColor] = useState<string | null>(initial?.color ?? null);

  const valid =
    name.trim() !== '' &&
    (!isQuantity || Number(targetValue) > 0) &&
    (scheduleType !== 'weekdays' || scheduleDays.length > 0) &&
    (scheduleType !== 'per_week' || (Number(timesPerWeek) >= 1 && Number(timesPerWeek) <= 7));

  const submit = () => {
    if (!valid) return;
    onSubmit({
      name: name.trim(),
      type: isQuantity ? 'quantity' : 'boolean',
      targetValue: isQuantity ? Number(targetValue) : null,
      unit: isQuantity && unit.trim() ? unit.trim() : null,
      scheduleType,
      scheduleDays: scheduleType === 'weekdays' ? [...scheduleDays].sort() : null,
      timesPerWeek: scheduleType === 'per_week' ? Number(timesPerWeek) : null,
      color,
    });
  };

  const toggleDay = (d: number) => {
    setScheduleDays(scheduleDays.includes(d) ? scheduleDays.filter((x) => x !== d) : [...scheduleDays, d]);
  };

  return (
    <div className="p-4 rounded-lg border border-white/10 bg-[var(--color-surface)] space-y-3">
      <input
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Escape') onCancel(); }}
        placeholder="Habit name…"
        className="w-full bg-transparent border border-white/20 rounded px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] outline-none focus:border-[var(--color-primary)]"
      />

      <div className="flex items-center gap-3 text-sm">
        <label className="flex items-center gap-1.5 text-[var(--color-text)] cursor-pointer">
          <input type="checkbox" checked={isQuantity} onChange={(e) => setIsQuantity(e.target.checked)} />
          Quantity
        </label>
        {isQuantity && (
          <>
            <input
              type="number"
              min="1"
              value={targetValue}
              onChange={(e) => setTargetValue(e.target.value)}
              placeholder="Target"
              className="w-20 bg-transparent border border-white/20 rounded px-2 py-1 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-primary)]"
            />
            <input
              value={unit}
              onChange={(e) => setUnit(e.target.value)}
              placeholder="Unit (optional)"
              className="w-28 bg-transparent border border-white/20 rounded px-2 py-1 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] outline-none focus:border-[var(--color-primary)]"
            />
          </>
        )}
      </div>

      <div className="flex items-center gap-2 text-sm flex-wrap">
        {(['daily', 'weekdays', 'per_week'] as const).map((st) => (
          <button
            key={st}
            onClick={() => setScheduleType(st)}
            className={`px-2 py-1 rounded border text-xs transition-colors ${
              scheduleType === st
                ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                : 'border-white/20 text-[var(--color-text-muted)] hover:bg-white/10'
            }`}
          >
            {st === 'daily' ? 'Daily' : st === 'weekdays' ? 'Specific days' : 'N× per week'}
          </button>
        ))}
        {scheduleType === 'weekdays' && (
          <div className="flex gap-1">
            {WEEKDAY_LABELS.map((label, d) => (
              <button
                key={d}
                onClick={() => toggleDay(d)}
                className={`w-8 py-1 rounded border text-xs transition-colors ${
                  scheduleDays.includes(d)
                    ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                    : 'border-white/20 text-[var(--color-text-muted)] hover:bg-white/10'
                }`}
              >
                {label[0]}
              </button>
            ))}
          </div>
        )}
        {scheduleType === 'per_week' && (
          <label className="flex items-center gap-1.5 text-[var(--color-text-muted)] text-xs">
            <input
              type="number"
              min="1"
              max="7"
              value={timesPerWeek}
              onChange={(e) => setTimesPerWeek(e.target.value)}
              className="w-14 bg-transparent border border-white/20 rounded px-2 py-1 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-primary)]"
            />
            times per week
          </label>
        )}
      </div>

      <div className="flex items-center gap-1.5">
        <button
          onClick={() => setColor(null)}
          title="Default color"
          className={`w-5 h-5 rounded-full border bg-[var(--color-primary)] ${color === null ? 'ring-2 ring-white/70' : 'border-white/20 opacity-60'}`}
        />
        {COLORS.map((c) => (
          <button
            key={c}
            onClick={() => setColor(c)}
            style={{ backgroundColor: c }}
            className={`w-5 h-5 rounded-full ${color === c ? 'ring-2 ring-white/70' : 'opacity-60 hover:opacity-100'}`}
          />
        ))}
      </div>

      {error && <div className="text-xs text-red-400">{error}</div>}

      <div className="flex gap-2">
        <button
          onClick={submit}
          disabled={!valid || pending}
          className="px-3 py-1.5 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-40 transition-colors text-sm"
        >
          {initial ? 'Save' : 'Add habit'}
        </button>
        <button
          onClick={onCancel}
          className="px-3 py-1.5 rounded text-[var(--color-text-muted)] hover:bg-white/10 transition-colors text-sm"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
