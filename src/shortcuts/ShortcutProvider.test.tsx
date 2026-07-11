// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ShortcutProvider, useShortcutScope } from './ShortcutProvider';
import type { ScopeHandlers } from './ShortcutProvider';

vi.mock('../hooks/api', () => ({
  api: { shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) } },
}));

// Registers a depth-1 scope like a view component would.
function Scope({ handlers }: { handlers: ScopeHandlers }) {
  useShortcutScope(1, handlers);
  return null;
}

function renderScope(handlers: ScopeHandlers) {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="journal" onViewChange={() => {}}>
        <Scope handlers={handlers} />
      </ShortcutProvider>
    </QueryClientProvider>,
  );
}

describe('W/S dispatch inside a scope', () => {
  it('moves the selection in a list scope, never falling through to scroll', () => {
    const next = vi.fn();
    const prev = vi.fn();
    const scrollDown = vi.fn();
    const scrollUp = vi.fn();
    renderScope({ next, prev, scrollDown, scrollUp });

    fireEvent.keyDown(window, { code: 'KeyD' }); // descend from sidebar level into the scope
    fireEvent.keyDown(window, { code: 'KeyS' });
    fireEvent.keyDown(window, { code: 'KeyW' });

    expect(next).toHaveBeenCalledTimes(1);
    expect(prev).toHaveBeenCalledTimes(1);
    expect(scrollDown).not.toHaveBeenCalled();
    expect(scrollUp).not.toHaveBeenCalled();
  });

  it('scrolls a content-only scope that has no next/prev', () => {
    const scrollDown = vi.fn();
    const scrollUp = vi.fn();
    renderScope({ scrollDown, scrollUp });

    fireEvent.keyDown(window, { code: 'KeyD' }); // descend from sidebar level into the scope
    fireEvent.keyDown(window, { code: 'KeyS' });
    fireEvent.keyDown(window, { code: 'KeyW' });

    expect(scrollDown).toHaveBeenCalledTimes(1);
    expect(scrollUp).toHaveBeenCalledTimes(1);
  });
});

describe('F search shortcut', () => {
  it('invokes the search handler even from the sidebar level', () => {
    const search = vi.fn();
    renderScope({ search });

    fireEvent.keyDown(window, { code: 'KeyF' });

    expect(search).toHaveBeenCalledTimes(1);
  });

  it('stays inert while typing in an input', () => {
    const search = vi.fn();
    const { container } = renderScope({ search });
    const input = document.createElement('input');
    container.appendChild(input);
    input.focus();

    fireEvent.keyDown(input, { code: 'KeyF' });

    expect(search).not.toHaveBeenCalled();
  });
});
