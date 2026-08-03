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
  lifestyleNeedsAttention?: boolean,
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
          lifestyleNeedsAttention={lifestyleNeedsAttention}
          newspapersNeedAttention={newspapersNeedAttention}
        />
      </ShortcutProvider>
    </QueryClientProvider>
  );
}

describe('Sidebar selfie badge', () => {
  it('shows no badge by default', () => {
    renderSidebar(false);
    expect(screen.queryByTitle(/No selfie logged today/)).toBeNull();
  });

  it('shows the exclamation badge when today has no selfie', () => {
    renderSidebar(true);
    expect(screen.queryByTitle(/No selfie logged today/)).toBeTruthy();
  });
});

describe('Sidebar newspapers badge', () => {
  it('shows no badge by default', () => {
    renderSidebar(false, false);
    expect(screen.queryByTitle(/haven't all synced/)).toBeNull();
  });

  it('shows the exclamation badge when today has a missing edition', () => {
    renderSidebar(false, true);
    expect(screen.queryByTitle(/haven't all synced/)).toBeTruthy();
  });
});
