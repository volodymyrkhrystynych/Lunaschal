// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ShortcutProvider, useShortcutScope } from './ShortcutProvider';
import type { ScopeHandlers } from './ShortcutProvider';

vi.mock('../hooks/api', () => ({
  api: { shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) } },
}));

// Registers a scope like a view component would (depth 1 unless overridden).
function Scope({
  handlers,
  depth = 1,
}: {
  handlers: ScopeHandlers;
  depth?: number;
}) {
  useShortcutScope(depth, handlers);
  return null;
}

function renderScope(
  handlers: ScopeHandlers,
  onToggleSidebar?: () => void,
  deepHandlers?: ScopeHandlers
) {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider
        currentView="journal"
        onViewChange={() => {}}
        onToggleSidebar={onToggleSidebar}
      >
        <Scope handlers={handlers} />
        {deepHandlers && <Scope handlers={deepHandlers} depth={2} />}
      </ShortcutProvider>
    </QueryClientProvider>
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

describe('B sidebar toggle shortcut', () => {
  it('toggles the sidebar from the sidebar level', () => {
    const toggle = vi.fn();
    renderScope({}, toggle);

    fireEvent.keyDown(window, { code: 'KeyB' });

    expect(toggle).toHaveBeenCalledTimes(1);
  });

  it('toggles the sidebar even from inside a tab scope', () => {
    const toggle = vi.fn();
    renderScope({ next: vi.fn() }, toggle);

    fireEvent.keyDown(window, { code: 'KeyD' }); // descend into the scope
    fireEvent.keyDown(window, { code: 'KeyB' });

    expect(toggle).toHaveBeenCalledTimes(1);
  });

  it('stays inert while typing in an input', () => {
    const toggle = vi.fn();
    const { container } = renderScope({}, toggle);
    const input = document.createElement('input');
    container.appendChild(input);
    input.focus();

    fireEvent.keyDown(input, { code: 'KeyB' });

    expect(toggle).not.toHaveBeenCalled();
  });
});

describe('reader/writing font size and list-toggle shortcuts', () => {
  it('invokes fontUp/fontDown on =/- from the sidebar level', () => {
    const fontUp = vi.fn();
    const fontDown = vi.fn();
    renderScope({ fontUp, fontDown });

    fireEvent.keyDown(window, { code: 'Equal' });
    fireEvent.keyDown(window, { code: 'Minus' });

    expect(fontUp).toHaveBeenCalledTimes(1);
    expect(fontDown).toHaveBeenCalledTimes(1);
  });

  it('invokes toggleList on L', () => {
    const toggleList = vi.fn();
    renderScope({ toggleList });

    fireEvent.keyDown(window, { code: 'KeyL' });

    expect(toggleList).toHaveBeenCalledTimes(1);
  });

  it('falls back to the depth-1 handler from a deeper scope', () => {
    const fontUp = vi.fn();
    renderScope({ fontUp, next: vi.fn() }, undefined, { next: vi.fn() });

    fireEvent.keyDown(window, { code: 'KeyD' }); // level 1
    fireEvent.keyDown(window, { code: 'KeyD' }); // level 2 (deep scope has no fontUp)
    fireEvent.keyDown(window, { code: 'Equal' });

    expect(fontUp).toHaveBeenCalledTimes(1);
  });

  it('stays inert while typing in a textarea', () => {
    const fontUp = vi.fn();
    const toggleList = vi.fn();
    const { container } = renderScope({ fontUp, toggleList });
    const textarea = document.createElement('textarea');
    container.appendChild(textarea);
    textarea.focus();

    fireEvent.keyDown(textarea, { code: 'Equal' });
    fireEvent.keyDown(textarea, { code: 'KeyL' });

    expect(fontUp).not.toHaveBeenCalled();
    expect(toggleList).not.toHaveBeenCalled();
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
