import type { ChatTodoItem } from '../hooks/api';

/** A bar row tied to a to-do or habit: ticking either side ticks both, and
 * there is nothing to send to the permanent list (backend/day_plan.py). */
export function isLinkedChatTodo(
  todo: Pick<ChatTodoItem, 'sourceType' | 'sourceId'>
): boolean {
  return (
    (todo.sourceType === 'todo' || todo.sourceType === 'daily') &&
    !!todo.sourceId
  );
}

/** Where a bar row came from, in a word or two, or '' for one typed into the
 * bar. `ChatTodo.sourceLabel` on iOS, word for word. */
export function chatTodoSourceLabel(
  todo: Pick<ChatTodoItem, 'sourceType' | 'sourceId'>
): string {
  switch (todo.sourceType) {
    case 'todo':
      return todo.sourceId ? 'From your to-dos' : '';
    case 'daily':
      return todo.sourceId ? 'Daily habit' : '';
    case 'goal':
      return 'For a goal';
    case 'journal':
      return 'From your journal';
    default:
      return '';
  }
}
