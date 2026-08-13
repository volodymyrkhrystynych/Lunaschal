import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, TodoList } from '../../hooks/api';
import { useDailyToggle, useTodoUpdate } from '../../offline/mutationDefaults';
import { groupTodosByList, partitionTodos } from '../../lib/todos';
import {
  useShortcuts,
  useShortcutScope,
} from '../../shortcuts/ShortcutProvider';
import { CARD, CARD_DIVIDER } from '../Lifestyle/card';
import { DailyTasks } from './DailyTasks';
import { TodoSection } from './TodoSection';

// Keyboard sections in W/S order: the daily-task list, then the two todo lists.
// Level 1 picks a section, level 2 navigates the items inside it.
const SECTIONS = ['daily', 'todo', 'archive'] as const;
export type TaskSection = (typeof SECTIONS)[number];

/**
 * Daily tasks and to-dos, as a section of the Lifestyle tab rather than a tab of
 * its own.
 *
 * It used to be the Tasks view, with Chores as a third list that the Lifestyle
 * tab then rendered a second time in its own card. One screen now: the chores
 * list was folded into the to-dos (`normalize_list` in
 * backend/todo_recurrence.py still accepts the old name for offline replays),
 * and the whole thing sits directly under the activity heatmap — the two things
 * checked several times a day, first on the page on a phone.
 *
 * It keeps the shortcut scopes it always had (1 for sections, 2 for items);
 * nothing else in the Lifestyle tree registers one, so there's still exactly one
 * owner per scope number.
 */
export function TasksSection() {
  const [section, setSection] = useState<TaskSection>('daily');
  const [activeList, setActiveList] = useState<TodoList>('todo');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  // One toggle for the whole card: daily tasks and to-dos sit under one border,
  // so two independent 🗑 buttons would read as one control that half-works.
  const [showDelete, setShowDelete] = useState(false);
  const { level, setLevel } = useShortcuts();

  const { data: tasks = [], isLoading: tasksLoading } = useQuery({
    queryKey: ['tasks'],
    queryFn: api.tasks.list,
  });
  const { data: todos = [], isLoading: todosLoading } = useQuery({
    queryKey: ['todos'],
    queryFn: api.todos.list,
  });

  const sortedTasks = useMemo(
    () => [...tasks].sort((a, b) => a.position - b.position),
    [tasks]
  );
  const buckets = useMemo(() => groupTodosByList(todos), [todos]);
  // Completed todos no longer live in a list here — completing one drops a small
  // notification into the Journal feed instead (see task_events).
  const { active } = useMemo(
    () => partitionTodos(buckets[activeList]),
    [buckets, activeList]
  );
  const counts = useMemo(
    () => ({
      todo: buckets.todo.filter(t => !t.done).length,
      archive: buckets.archive.filter(t => !t.done).length,
    }),
    [buckets]
  );

  const visibleTodos = active;
  const visibleIds = useMemo(
    () =>
      section === 'daily'
        ? sortedTasks.map(t => t.id)
        : visibleTodos.map(t => t.id),
    [section, sortedTasks, visibleTodos]
  );

  // Offline-queueable (optimistic update + invalidation live in the registered
  // mutation defaults).
  const updateTodo = useTodoUpdate();
  const toggleDaily = useDailyToggle();

  // Selection belongs to one section; leaving it invalidates the highlight.
  useEffect(() => {
    setSelectedId(null);
  }, [section, activeList]);

  // Backing out of level 2 (Escape outside an input, A) closes the form.
  useEffect(() => {
    if (level < 2 && creating) setCreating(false);
  }, [level, creating]);

  const stepSection = (dir: -1 | 1) => {
    const idx = SECTIONS.indexOf(section);
    const next =
      SECTIONS[Math.min(Math.max(idx + dir, 0), SECTIONS.length - 1)];
    setSection(next);
    if (next !== 'daily') setActiveList(next);
  };

  const stepItem = (dir: -1 | 1) => {
    if (creating || visibleIds.length === 0) return;
    const idx = selectedId ? visibleIds.indexOf(selectedId) : -1;
    const next =
      idx === -1
        ? dir > 0
          ? 0
          : visibleIds.length - 1
        : Math.min(Math.max(idx + dir, 0), visibleIds.length - 1);
    const id = visibleIds[next];
    setSelectedId(id);
    document
      .getElementById(`todo-row-${id}`)
      ?.scrollIntoView({ block: 'nearest' });
  };

  const seedSelection = () => {
    setSelectedId(prev =>
      prev && visibleIds.includes(prev) ? prev : (visibleIds[0] ?? null)
    );
  };

  const startCreate = () => {
    if (section !== 'todo') return;
    setCreating(true);
    setLevel(2);
  };

  const moveSelected = () => {
    if (creating || !selectedId) return;
    if (section !== 'todo' && section !== 'archive') return;
    const idx = visibleIds.indexOf(selectedId);
    if (idx === -1) return;
    const remaining = visibleIds.filter(id => id !== selectedId);
    setSelectedId(remaining[Math.min(idx, remaining.length - 1)] ?? null);
    updateTodo.mutate({
      id: selectedId,
      data: { list: section === 'todo' ? 'archive' : 'todo' },
    });
  };

  const toggleSelectedDone = () => {
    if (creating || !selectedId) return;
    if (section === 'daily') {
      const task = sortedTasks.find(t => t.id === selectedId);
      if (task) toggleDaily.mutate({ id: task.id, done: task.done });
    } else {
      const todo = visibleTodos.find(t => t.id === selectedId);
      if (todo) updateTodo.mutate({ id: todo.id, data: { done: !todo.done } });
    }
  };

  useShortcutScope(1, {
    next: () => stepSection(1),
    prev: () => stepSection(-1),
    drillIn: () => {
      // To-Do drills straight into creation; Daily/Archive into item nav.
      if (section === 'todo') setCreating(true);
      else seedSelection();
      return false; // let the provider advance to level 2
    },
    create: startCreate,
  });

  useShortcutScope(2, {
    next: () => stepItem(1),
    prev: () => stepItem(-1),
    create: startCreate,
    drillOut: () => {
      if (creating) {
        setCreating(false);
        seedSelection();
        return true; // stay at level 2, now in item navigation
      }
      return false;
    },
    move: moveSelected,
    toggleDone: toggleSelectedDone,
  });

  const cancelCreate = () => {
    setCreating(false);
    seedSelection();
  };

  const selectList = (list: TodoList) => {
    setSection(list);
    setActiveList(list);
  };

  // One card, daily tasks above the to-dos. They were two side-by-side cards,
  // which gave a list capped at four items half the width of the tab and a
  // header of its own — the same "what am I doing today" question either way.
  return (
    <section className={CARD}>
      <DailyTasks
        tasks={sortedTasks}
        isLoading={tasksLoading}
        selectedId={section === 'daily' ? selectedId : null}
        itemNavActive={level >= 2 && section === 'daily'}
        sectionFocused={level === 1 && section === 'daily'}
        showDelete={showDelete}
        onToggleDelete={
          sortedTasks.length + active.length > 0
            ? () => setShowDelete(!showDelete)
            : null
        }
      />
      <div className={CARD_DIVIDER}>
        <TodoSection
          showDelete={showDelete}
          activeList={activeList}
          section={section}
          level={level}
          counts={counts}
          active={active}
          isLoading={todosLoading}
          selectedId={section !== 'daily' ? selectedId : null}
          creating={creating}
          onSelectList={selectList}
          onSelectTodo={id => {
            setSection(activeList);
            setSelectedId(id);
          }}
          onStartCreate={() => {
            setCreating(true);
            setLevel(2);
          }}
          onCancelCreate={cancelCreate}
          onUpdateTodo={(id, data) => updateTodo.mutate({ id, data })}
        />
      </div>
    </section>
  );
}
