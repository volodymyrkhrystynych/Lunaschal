// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ShortcutProvider } from '../shortcuts/ShortcutProvider';
import { Sidebar } from './Sidebar';

vi.mock('../hooks/api', () => ({
  api: {
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
  },
}));

function renderSidebar(
  lifestyleReasons?: string[],
  newspapersNeedAttention?: boolean
) {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider
        currentView="chat"
        onViewChange={() => {}}
        onToggleSidebar={() => {}}
      >
        <Sidebar
          currentView="chat"
          onViewChange={() => {}}
          isOpen
          onToggle={() => {}}
          lifestyleReasons={lifestyleReasons}
          newspapersNeedAttention={newspapersNeedAttention}
        />
      </ShortcutProvider>
    </QueryClientProvider>
  );
}

describe('Sidebar lifestyle badge', () => {
  it('shows no badge by default', () => {
    renderSidebar([]);
    expect(screen.queryByTitle(/No selfie logged today/)).toBeNull();
    expect(screen.queryByTitle(/Under 1,500 calories/)).toBeNull();
  });

  it('shows the exclamation badge when today has no selfie', () => {
    renderSidebar(['No selfie logged today']);
    expect(screen.queryByTitle(/No selfie logged today/)).toBeTruthy();
  });

  it('shows the exclamation badge when today is under the calorie floor', () => {
    renderSidebar(['Under 1,500 calories logged today']);
    expect(screen.queryByTitle(/Under 1,500 calories/)).toBeTruthy();
  });

  it('combines both reasons into one tooltip when both apply', () => {
    renderSidebar([
      'No selfie logged today',
      'Under 1,500 calories logged today',
    ]);
    const badge = screen.getByTitle(
      /No selfie logged today · Under 1,500 calories logged today/
    );
    expect(badge).toBeTruthy();
  });
});

describe('Sidebar newspapers badge', () => {
  it('shows no badge by default', () => {
    renderSidebar([], false);
    expect(screen.queryByTitle(/haven't all synced/)).toBeNull();
  });

  it('shows the exclamation badge when today has a missing edition', () => {
    renderSidebar([], true);
    expect(screen.queryByTitle(/haven't all synced/)).toBeTruthy();
  });
});
