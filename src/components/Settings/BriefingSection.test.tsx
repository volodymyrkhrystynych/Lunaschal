// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BriefingSection } from './BriefingSection';
import { api, type AppSettings } from '../../hooks/api';

vi.mock('../../hooks/api', () => ({
  api: {
    settings: {
      get: vi.fn(),
      llamaModels: vi.fn(),
      updateAI: vi.fn(),
    },
    chat: { runBriefing: vi.fn() },
  },
}));

function settings(over: Partial<AppSettings> = {}) {
  return {
    llamaUrl: '',
    briefingEnabled: true,
    briefingHour: 5,
    briefingModel: null,
    briefingThinking: true,
    briefingMaxTokens: 16384,
    briefingThinkingBudget: 6144,
    ...over,
  } as AppSettings;
}

function renderSection() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <BriefingSection />
    </QueryClientProvider>
  );
}

const budgetInput = () =>
  screen.getByLabelText(/thinking budget/i) as HTMLInputElement;

describe('BriefingSection thinking budget', () => {
  beforeEach(() => {
    vi.mocked(api.settings.updateAI).mockReset();
    vi.mocked(api.settings.updateAI).mockResolvedValue({ success: true });
  });

  it('is hidden while thinking is off', async () => {
    vi.mocked(api.settings.get).mockResolvedValue(
      settings({ briefingThinking: false })
    );
    renderSection();
    await screen.findByText(/think before writing/i);
    expect(screen.queryByLabelText(/thinking budget/i)).toBeNull();
  });

  it('shows the saved budget and saves a new one on blur', async () => {
    vi.mocked(api.settings.get).mockResolvedValue(settings());
    renderSection();
    await waitFor(() => expect(budgetInput().value).toBe('6144'));

    fireEvent.change(budgetInput(), { target: { value: '4096' } });
    fireEvent.blur(budgetInput());

    await waitFor(() => expect(api.settings.updateAI).toHaveBeenCalled());
    expect(vi.mocked(api.settings.updateAI).mock.calls[0][0]).toEqual({
      briefingThinkingBudget: 4096,
    });
  });

  it('clamps an out-of-range budget to the server range', async () => {
    vi.mocked(api.settings.get).mockResolvedValue(settings());
    renderSection();
    await waitFor(() => expect(budgetInput().value).toBe('6144'));

    fireEvent.change(budgetInput(), { target: { value: '99999' } });
    fireEvent.blur(budgetInput());

    await waitFor(() => expect(api.settings.updateAI).toHaveBeenCalled());
    expect(vi.mocked(api.settings.updateAI).mock.calls[0][0]).toEqual({
      briefingThinkingBudget: 32768,
    });
    expect(budgetInput().value).toBe('32768');
  });

  it('does not save when the value is unchanged', async () => {
    vi.mocked(api.settings.get).mockResolvedValue(settings());
    renderSection();
    await waitFor(() => expect(budgetInput().value).toBe('6144'));

    fireEvent.blur(budgetInput());
    expect(api.settings.updateAI).not.toHaveBeenCalled();
  });
});
