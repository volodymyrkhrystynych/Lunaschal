// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, ProposedTodo } from '../hooks/api';
import { BriefingTodos } from './BriefingTodos';

vi.mock('../hooks/api', () => ({
  api: {
    chat: { decideBriefingTodos: vi.fn().mockResolvedValue({ created: 0 }) },
  },
}));

const proposal = (
  id: string,
  title: string,
  extra: Partial<ProposedTodo> = {}
): ProposedTodo => ({
  id,
  title,
  list: 'todo',
  priority: 3,
  due: null,
  status: 'pending',
  linkedType: null,
  linkedId: null,
  linkedTitle: null,
  resolvedAt: null,
  ...extra,
});

beforeEach(() => {
  vi.clearAllMocks();
});

function renderPlan(proposals: ProposedTodo[]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BriefingTodos messageId="m1" proposals={proposals} />
    </QueryClientProvider>
  );
}

const decide = () => vi.mocked(api.chat.decideBriefingTodos);

describe('pending items', () => {
  it('offers Done, Dismiss and Add to to-dos on an unlinked item', () => {
    renderPlan([proposal('p1', 'Draft the report')]);

    expect(screen.getByText("Today's plan — 1 to go")).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Done' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Dismiss' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Add to to-dos' })).toBeTruthy();
    // Unlinked items stay editable.
    expect(screen.getByLabelText('To-do title')).toBeTruthy();
  });

  it('crossing one off posts a done decision for that id', async () => {
    renderPlan([proposal('p1', 'Draft the report')]);

    fireEvent.click(screen.getByRole('button', { name: 'Done' }));

    await waitFor(() =>
      expect(decide()).toHaveBeenCalledWith('m1', [
        { id: 'p1', action: 'done' },
      ])
    );
  });

  it('sends inline edits when adding to the to-do list', async () => {
    renderPlan([proposal('p1', 'Draft the report')]);

    fireEvent.change(screen.getByLabelText('To-do title'), {
      target: { value: 'Draft the Q3 report' },
    });
    fireEvent.change(screen.getByLabelText('Priority'), {
      target: { value: '5' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add to to-dos' }));

    await waitFor(() =>
      expect(decide()).toHaveBeenCalledWith('m1', [
        {
          id: 'p1',
          action: 'accept',
          title: 'Draft the Q3 report',
          priority: 5,
          due: null,
          list: 'todo',
        },
      ])
    );
  });
});

describe('linked items', () => {
  it('hides Add to to-dos and shows what it is tied to', () => {
    renderPlan([
      proposal('p1', 'Buy groceries', {
        linkedType: 'todo',
        linkedId: 't1',
        linkedTitle: 'Get groceries',
      }),
    ]);

    // It's already on a list, so adding it again is not on offer.
    expect(screen.queryByRole('button', { name: 'Add to to-dos' })).toBeNull();
    expect(screen.getByText('on your to-dos')).toBeTruthy();
    // The title is fixed — editing it would break the tie to the real row.
    expect(screen.queryByLabelText('To-do title')).toBeNull();
    expect(screen.getByText('Buy groceries')).toBeTruthy();
    // Still crossable off.
    expect(screen.getByRole('button', { name: 'Done' })).toBeTruthy();
  });

  it('labels a daily-task link distinctly', () => {
    renderPlan([
      proposal('p1', 'Do your stretches', {
        linkedType: 'daily',
        linkedId: 'd1',
        linkedTitle: 'Stretch',
      }),
    ]);
    expect(screen.getByText('daily task')).toBeTruthy();
  });

  it('leaves linked items out of "Add all to to-dos"', async () => {
    renderPlan([
      proposal('p1', 'Buy groceries', { linkedType: 'todo', linkedId: 't1' }),
      proposal('p2', 'Call the vet'),
      proposal('p3', 'Book the flight'),
    ]);

    fireEvent.click(screen.getByRole('button', { name: 'Add all to to-dos' }));

    await waitFor(() => expect(decide()).toHaveBeenCalled());
    const [, decisions] = decide().mock.calls[0];
    expect(decisions.map(d => d.id)).toEqual(['p2', 'p3']);
  });
});

describe('resolved items', () => {
  it('records the outcome and when it happened, with no buttons', () => {
    const at = new Date('2026-07-14T14:20:00').getTime() / 1000;
    renderPlan([
      proposal('p1', 'Draft the report', { status: 'done', resolvedAt: at }),
      proposal('p2', 'Call the vet', { status: 'rejected', resolvedAt: at }),
      proposal('p3', 'Book the flight', { status: 'accepted', resolvedAt: at }),
    ]);

    expect(screen.getByText(/^Done · Jul 14, 2:20 PM$/)).toBeTruthy();
    expect(screen.getByText(/^Dismissed · Jul 14, 2:20 PM$/)).toBeTruthy();
    expect(
      screen.getByText(/^Added to to-dos · Jul 14, 2:20 PM$/)
    ).toBeTruthy();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('renders a legacy duplicate card with no link fields or timestamp', () => {
    // A briefing written before linking existed: no linkedType, no resolvedAt.
    const legacy = {
      id: 'p1',
      title: 'Buy milk',
      list: 'todo',
      priority: 3,
      due: null,
      status: 'duplicate',
    } as ProposedTodo;
    renderPlan([legacy]);

    expect(screen.getByText('Buy milk')).toBeTruthy();
    // Label only — no trailing " · <time>" separator.
    expect(screen.getByText('Already on your list — skipped')).toBeTruthy();
  });
});
