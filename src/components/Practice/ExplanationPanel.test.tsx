// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest';
import { render, fireEvent, screen } from '@testing-library/react';
import type { PracticeExplanation } from '../../hooks/api';
import { ExplanationPanel } from './ExplanationPanel';

const explanation: PracticeExplanation = {
  summary: 'Holds a value that survives a re-render.',
  parts: [{ name: 'useState', detail: 'Returns the value and its setter.' }],
  related: '`useReducer` for anything with more than one transition.',
};

// The drills are keyed on the snippet id, so a new snippet is a fresh mount of
// this component — which is exactly what used to lose the open state. Every
// "next snippet" assertion below has to unmount and mount again to be testing
// the real thing.
function mount(defaultOpen = false) {
  return render(
    <ExplanationPanel explanation={explanation} defaultOpen={defaultOpen} />
  );
}

const isOpen = () => screen.queryByText(explanation.summary) !== null;
const toggleButton = () =>
  screen.getByRole('button', { name: /what this is/i });

beforeEach(() => {
  localStorage.clear();
});

describe('ExplanationPanel', () => {
  it('stays open on the next snippet once it has been opened', () => {
    const { unmount } = mount();
    expect(isOpen()).toBe(false);
    fireEvent.click(toggleButton());
    expect(isOpen()).toBe(true);

    unmount();
    mount();
    expect(isOpen()).toBe(true);
  });

  it('stays closed on the next snippet once it has been closed', () => {
    const { unmount } = mount(true);
    expect(isOpen()).toBe(true);
    fireEvent.click(toggleButton());
    expect(isOpen()).toBe(false);

    unmount();
    // Even where the drill would rather have it open: a graded recall passes
    // defaultOpen, and the user's own choice has to beat it.
    mount(true);
    expect(isOpen()).toBe(false);
  });

  it('survives a reload, not just a remount', () => {
    mount();
    fireEvent.click(toggleButton());
    // Nothing but localStorage carries between two page loads, so the stored
    // value is the whole mechanism — assert it directly.
    expect(localStorage.getItem('lunaschal:practiceExplanationOpen')).toBe('1');
  });

  it('falls back to the drill default until a choice has been made', () => {
    const { unmount } = mount(false);
    expect(isOpen()).toBe(false); // speed drill: out of the way while typing
    unmount();

    mount(true);
    expect(isOpen()).toBe(true); // graded recall: the payoff, opened for you
  });

  it('renders nothing when the snippet has no explanation', () => {
    const { container } = render(<ExplanationPanel explanation={null} />);
    expect(container.innerHTML).toBe('');
  });

  it('renders inline code and emphasis rather than their markers', () => {
    mount(true);
    expect(screen.getByText('useReducer').tagName).toBe('CODE');
    expect(document.body.textContent).not.toContain('`useReducer`');
  });
});
