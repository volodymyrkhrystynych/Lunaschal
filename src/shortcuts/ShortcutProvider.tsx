import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../hooks/api';
import { DEFAULT_BINDINGS, comboFromEvent, isEditableTarget, keyCapture } from './keymap';
import type { ActionId } from './keymap';

export type AppView = 'chat' | 'journal' | 'meetings' | 'calendar' | 'learning' | 'settings' | 'files' | 'writing' | 'tasks' | 'cookbook' | 'fanfic' | 'newspapers';

export const VIEW_ORDER: AppView[] = ['chat', 'tasks', 'journal', 'meetings', 'writing', 'calendar', 'learning', 'cookbook', 'fanfic', 'newspapers', 'files', 'settings'];

const TAB_ACTIONS: Partial<Record<ActionId, AppView>> = {
  'tab.chat': 'chat',
  'tab.tasks': 'tasks',
  'tab.journal': 'journal',
  'tab.writing': 'writing',
  'tab.calendar': 'calendar',
  'tab.learning': 'learning',
  'tab.cookbook': 'cookbook',
  'tab.fanfic': 'fanfic',
  'tab.files': 'files',
  'tab.settings': 'settings',
};

export interface ScopeHandlers {
  next?: () => void;
  prev?: () => void;
  drillIn?: () => boolean | void;
  drillOut?: () => boolean | void;
  create?: () => void;
  createAlt?: () => void;
  scrollDown?: () => void;
  scrollUp?: () => void;
  annotate?: () => void;
  search?: () => void;
  approve?: () => void;
  deny?: () => void;
  record?: () => void;
  fontUp?: () => void;
  fontDown?: () => void;
  toggleList?: () => void;
}

interface ShortcutContextValue {
  bindings: Record<ActionId, string>;
  level: number;
  setLevel: (n: number) => void;
  registerScope: (depth: number, handlers: ScopeHandlers) => () => void;
  requestCreate: (view: AppView) => void;
}

const ShortcutContext = createContext<ShortcutContextValue | null>(null);

export function useShortcuts(): ShortcutContextValue {
  const ctx = useContext(ShortcutContext);
  if (!ctx) throw new Error('useShortcuts must be used within ShortcutProvider');
  return ctx;
}

interface ShortcutProviderProps {
  currentView: AppView;
  onViewChange: (view: AppView) => void;
  onToggleSidebar?: () => void;
  children: ReactNode;
}

export function ShortcutProvider({ currentView, onViewChange, onToggleSidebar, children }: ShortcutProviderProps) {
  const [level, setLevel] = useState(0);
  const scopesRef = useRef(new Map<number, ScopeHandlers[]>());
  const pendingCreateRef = useRef<AppView | null>(null);
  const currentViewRef = useRef(currentView);
  currentViewRef.current = currentView;

  const { data } = useQuery({ queryKey: ['shortcuts'], queryFn: api.shortcuts.get });

  const bindings = useMemo(() => {
    const merged = { ...DEFAULT_BINDINGS };
    const saved = data?.bindings ?? {};
    for (const k of Object.keys(merged) as ActionId[]) {
      const v = saved[k];
      if (typeof v === 'string' && v) merged[k] = v;
    }
    return merged;
  }, [data]);

  const comboToAction = useMemo(() => {
    const m: Record<string, ActionId> = {};
    for (const [action, combo] of Object.entries(bindings)) m[combo] = action as ActionId;
    return m;
  }, [bindings]);

  // Back to sidebar level whenever the view changes
  useEffect(() => {
    setLevel(0);
  }, [currentView]);

  const registerScope = useCallback((depth: number, handlers: ScopeHandlers) => {
    let list = scopesRef.current.get(depth);
    if (!list) {
      list = [];
      scopesRef.current.set(depth, list);
    }
    list.push(handlers);
    // Fulfill a pending create (e.g. global "new journal entry" just switched views)
    if (depth === 1 && handlers.create && pendingCreateRef.current === currentViewRef.current) {
      pendingCreateRef.current = null;
      const create = handlers.create;
      setTimeout(() => create(), 0);
    }
    return () => {
      const arr = scopesRef.current.get(depth);
      if (arr) {
        const i = arr.indexOf(handlers);
        if (i >= 0) arr.splice(i, 1);
        if (arr.length === 0) scopesRef.current.delete(depth);
      }
    };
  }, []);

  const requestCreate = useCallback((view: AppView) => {
    const handler = currentViewRef.current === view ? resolveHandler(scopesRef.current, 1, 'create') : null;
    if (handler) {
      (handler as () => void)();
    } else {
      pendingCreateRef.current = view;
    }
  }, []);

  // Dispatch lives in a ref so the single window listener always sees fresh state.
  const dispatchRef = useRef<(e: KeyboardEvent) => void>(() => {});
  dispatchRef.current = (e: KeyboardEvent) => {
    if (keyCapture.active || e.defaultPrevented) return;

    if (e.key === 'Escape') {
      const focused = document.activeElement;
      if (isEditableTarget(focused)) {
        (focused as HTMLElement).blur();
        return; // no stopPropagation — per-input Escape handlers still run
      }
      setLevel((l) => Math.max(0, l - 1));
      return;
    }

    if (isEditableTarget(e.target)) return;

    const action = comboToAction[comboFromEvent(e)];
    if (!action) return;

    const scopes = scopesRef.current;
    let lvl = level;
    while (lvl > 0 && !scopes.has(lvl)) lvl--;
    if (lvl !== level) setLevel(lvl);

    let handled = true;

    const tabView = TAB_ACTIONS[action];
    if (tabView) {
      onViewChange(tabView);
      setLevel(0);
    } else if (action === 'global.newJournalEntry') {
      onViewChange('journal');
      requestCreate('journal');
    } else if (action === 'global.toggleSidebar') {
      if (onToggleSidebar) onToggleSidebar();
      else handled = false;
    } else if (action === 'nav.up' || action === 'nav.down') {
      if (lvl === 0) {
        const idx = VIEW_ORDER.indexOf(currentViewRef.current);
        const next = action === 'nav.down' ? Math.min(idx + 1, VIEW_ORDER.length - 1) : Math.max(idx - 1, 0);
        if (next !== idx) onViewChange(VIEW_ORDER[next]);
      } else {
        // Lists move their selection; content-only scopes scroll instead.
        const handler =
          resolveHandler(scopes, lvl, action === 'nav.down' ? 'next' : 'prev') ??
          resolveHandler(scopes, lvl, action === 'nav.down' ? 'scrollDown' : 'scrollUp');
        if (handler) (handler as () => void)();
      }
    } else if (action === 'nav.in') {
      const drillIn = resolveHandler(scopes, lvl, 'drillIn') as (() => boolean | void) | null;
      const consumed = lvl > 0 && drillIn ? drillIn() : false;
      if (!consumed && scopes.has(lvl + 1)) setLevel(lvl + 1);
    } else if (action === 'nav.out') {
      const drillOut = lvl > 0 ? (resolveHandler(scopes, lvl, 'drillOut') as (() => boolean | void) | null) : null;
      const consumed = drillOut ? drillOut() : false;
      if (!consumed) setLevel(Math.max(0, lvl - 1));
    } else if (action === 'action.new' || action === 'action.newAlt') {
      const handler = resolveHandler(scopes, Math.max(lvl, 1), action === 'action.new' ? 'create' : 'createAlt');
      if (handler) (handler as () => void)();
      else handled = false;
    } else if (action === 'action.annotate') {
      const handler = resolveHandler(scopes, Math.max(lvl, 1), 'annotate');
      if (handler) (handler as () => void)();
      else handled = false;
    } else if (action === 'action.search') {
      const handler = resolveHandler(scopes, Math.max(lvl, 1), 'search');
      if (handler) (handler as () => void)();
      else handled = false;
    } else if (action === 'learning.approve' || action === 'learning.deny') {
      // Only fire at the depth where the selection highlight is visible.
      const handler = resolveHandler(scopes, Math.max(lvl, 1), action === 'learning.approve' ? 'approve' : 'deny');
      if (handler) (handler as () => void)();
      else handled = false;
    } else if (action === 'learning.record') {
      // Recording has no selection to disambiguate, so it works from any depth.
      const maxDepth = Math.max(lvl, 1, ...Array.from(scopes.keys()));
      const handler = resolveHandlerDeep(scopes, maxDepth, 'record');
      if (handler) (handler as () => void)();
      else handled = false;
    } else if (action === 'reader.fontUp' || action === 'reader.fontDown' || action === 'reader.toggleList') {
      const method =
        action === 'reader.fontUp' ? 'fontUp' : action === 'reader.fontDown' ? 'fontDown' : 'toggleList';
      // These handlers live at depth 1 but should work from any depth in the view.
      const handler = resolveHandlerDeep(scopes, Math.max(lvl, 1), method);
      if (handler) (handler as () => void)();
      else handled = false;
    } else {
      handled = false;
    }

    if (handled) {
      e.preventDefault();
      e.stopPropagation();
    }
  };

  useEffect(() => {
    const listener = (e: KeyboardEvent) => dispatchRef.current(e);
    window.addEventListener('keydown', listener, true);
    return () => window.removeEventListener('keydown', listener, true);
  }, []);

  const value = useMemo(
    () => ({ bindings, level, setLevel, registerScope, requestCreate }),
    [bindings, level, registerScope, requestCreate],
  );

  return <ShortcutContext.Provider value={value}>{children}</ShortcutContext.Provider>;
}

function resolveHandler(
  scopes: Map<number, ScopeHandlers[]>,
  depth: number,
  method: keyof ScopeHandlers,
): ScopeHandlers[keyof ScopeHandlers] | null {
  const list = scopes.get(depth);
  if (!list) return null;
  for (let i = list.length - 1; i >= 0; i--) {
    const fn = list[i][method];
    if (fn) return fn;
  }
  return null;
}

function resolveHandlerDeep(
  scopes: Map<number, ScopeHandlers[]>,
  depth: number,
  method: keyof ScopeHandlers,
): ScopeHandlers[keyof ScopeHandlers] | null {
  for (let d = depth; d >= 1; d--) {
    const fn = resolveHandler(scopes, d, method);
    if (fn) return fn;
  }
  return null;
}

/**
 * Register navigation/create handlers for a focus depth (1 = inside the tab,
 * 2+ = deeper). Handlers are kept in a ref so callers can pass fresh closures
 * every render without re-registering.
 */
export function useShortcutScope(depth: number, handlers: ScopeHandlers) {
  const { registerScope } = useShortcuts();
  const ref = useRef(handlers);
  ref.current = handlers;
  useEffect(() => {
    const proxy: ScopeHandlers = {};
    for (const key of Object.keys(ref.current) as (keyof ScopeHandlers)[]) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (proxy as any)[key] = (...args: unknown[]) => (ref.current[key] as any)?.(...args);
    }
    return registerScope(depth, proxy);
  }, [depth, registerScope]);
}
