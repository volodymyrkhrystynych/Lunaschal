// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AgentSteps } from './AgentSteps';

describe('AgentSteps', () => {
  it('renders nothing at all for a turn with no steps and no reasoning', () => {
    const { container } = render(<AgentSteps steps={[]} />);
    expect(container.innerHTML).toBe('');
  });

  it('hangs the reasoning off the step trace as a nested disclosure', () => {
    render(
      <AgentSteps
        steps={[{ tool: 'web_search', arg: 'fsrs', ok: true, count: 2 }]}
        thinking="they probably mean the scheduler"
      />
    );

    const trace = screen.getByText('1 step').closest('details');
    const reasoning = screen.getByText('Reasoning').closest('details');
    expect(reasoning).not.toBeNull();
    // Inside the trace, not a sibling of it: the two answer the same question,
    // and a second top-level disclosure per reply is one more thing to open.
    expect(trace?.contains(reasoning as Node)).toBe(true);
    expect(screen.getByText('they probably mean the scheduler')).toBeTruthy();
  });

  it('shows reasoning on its own when the turn used no tools', () => {
    // The whole case this exists for: a reply that thought and then said
    // nothing. Wrapping it in a "0 steps" disclosure would bury the only
    // account of the turn behind an empty one.
    render(<AgentSteps steps={[]} thinking="going round in circles" />);

    expect(screen.queryByText(/step/)).toBeNull();
    expect(screen.getByText('Reasoning')).toBeTruthy();
  });

  it('does not offer a Reasoning disclosure when nothing was captured', () => {
    render(<AgentSteps steps={[{ tool: 'web_fetch', arg: 'x', ok: true }]} />);

    expect(screen.getByText('1 step')).toBeTruthy();
    expect(screen.queryByText('Reasoning')).toBeNull();
  });
});
