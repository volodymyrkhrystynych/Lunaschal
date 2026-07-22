import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, TodoList } from '../../hooks/api';
import { useDailyToggle, useTodoUpdate } from '../../offline/mutationDefaults';
import { groupTodosByList, partitionTodos } from '../../lib/todos';
import {
  useShortcuts,
  useShortcutScope,
} from '../../shortcuts/ShortcutProvider';
import { DailyTasks } from './DailyTasks';
import { TodoSection } from './TodoSection';

// Keyboard sections in W/S order: the daily-task list, then the three todo
// lists. Level 1 picks a section, level 2 navigates the items inside it.
const SECTIONS = ['daily', 'todo', 'chores', 'archive'] as const;
export type TaskSection = (typeof SECTIONS)[number];

export function Tasks() {
  const [section, setSection] = useState<TaskSection>('daily');
  const [activeList, setActiveList] = useState<TodoList>('todo');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [showCompleted, setShowCompleted] = useState(false);
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
  const { active, completed } = useMemo(
    () => partitionTodos(buckets[activeList]),
    [buckets, activeList]
  );
  const counts = useMemo(
    () => ({
      todo: buckets.todo.filter(t => !t.done).length,
      chores: buckets.chores.filter(t => !t.done).length,
      archive: buckets.archive.filter(t => !t.done).length,
    }),
    [buckets]
  );

  const visibleTodos = useMemo(
    () => (showCompleted ? [...active, ...completed] : active),
    [active, completed, showCompleted]
  );
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
    if (section !== 'todo' && section !== 'chores') return;
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
      // Todo/Chores drill straight into creation; Daily/Archive into item nav.
      if (section === 'todo' || section === 'chores') setCreating(true);
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

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-xl mx-auto">
        <DailyTasks
          tasks={sortedTasks}
          isLoading={tasksLoading}
          selectedId={section === 'daily' ? selectedId : null}
          itemNavActive={level >= 2 && section === 'daily'}
          sectionFocused={level === 1 && section === 'daily'}
        />
        <TodoSection
          activeList={activeList}
          section={section}
          level={level}
          counts={counts}
          active={active}
          completed={completed}
          isLoading={todosLoading}
          selectedId={section !== 'daily' ? selectedId : null}
          creating={creating}
          showCompleted={showCompleted}
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
          onToggleCompleted={() => setShowCompleted(v => !v)}
          onUpdateTodo={(id, data) => updateTodo.mutate({ id, data })}
        />
      </div>
    </div>
  );
}
