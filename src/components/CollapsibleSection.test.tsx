// @vitest-environment jsdom
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { CollapsibleSection } from './CollapsibleSection';

describe('CollapsibleSection', () => {
  it('starts collapsed', () => {
    // The General tab holds fifteen of these; expanded-by-default opened the
    // page as a wall of controls.
    render(
      <CollapsibleSection title="Weather">
        <p>inner</p>
      </CollapsibleSection>
    );

    expect(
      screen
        .getByRole('button', { name: /Weather/ })
        .getAttribute('aria-expanded')
    ).toBe('false');
  });

  it('expands and collapses on click', () => {
    render(
      <CollapsibleSection title="Weather">
        <p>inner</p>
      </CollapsibleSection>
    );
    const toggle = screen.getByRole('button', { name: /Weather/ });

    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');

    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
  });

  it('honours an explicit defaultExpanded', () => {
    render(
      <CollapsibleSection title="Weather" defaultExpanded>
        <p>inner</p>
      </CollapsibleSection>
    );

    expect(
      screen
        .getByRole('button', { name: /Weather/ })
        .getAttribute('aria-expanded')
    ).toBe('true');
  });

  it('opens when autoExpand turns true after mount', () => {
    // The flag depends on a fetch, so it is false on the first render — the
    // case a plain defaultExpanded cannot cover.
    const { rerender } = render(
      <CollapsibleSection title="Backup" autoExpand={false}>
        <p>inner</p>
      </CollapsibleSection>
    );
    const toggle = screen.getByRole('button', { name: /Backup/ });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');

    rerender(
      <CollapsibleSection title="Backup" autoExpand>
        <p>inner</p>
      </CollapsibleSection>
    );
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
  });

  it('does not fight a user who collapses an auto-expanded section', () => {
    // The status is polled, so autoExpand stays true. Re-expanding on every
    // poll would make the section impossible to close.
    const { rerender } = render(
      <CollapsibleSection title="Backup" autoExpand={false}>
        <p>inner</p>
      </CollapsibleSection>
    );
    rerender(
      <CollapsibleSection title="Backup" autoExpand>
        <p>inner</p>
      </CollapsibleSection>
    );

    const toggle = screen.getByRole('button', { name: /Backup/ });
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');

    rerender(
      <CollapsibleSection title="Backup" autoExpand>
        <p>inner</p>
      </CollapsibleSection>
    );
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
  });

  it('in controlled mode, reflects the open prop rather than owning its own state', () => {
    const onToggle = vi.fn();
    const { rerender } = render(
      <CollapsibleSection title="Recipe" open={false} onToggle={onToggle}>
        <p>inner</p>
      </CollapsibleSection>
    );
    const toggle = screen.getByRole('button', { name: /Recipe/ });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');

    fireEvent.click(toggle);
    // A click asks the parent to toggle; it does not flip state on its own —
    // that would fight a caller like RecipeList tracking one open row at a time.
    expect(onToggle).toHaveBeenCalledWith(true);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');

    rerender(
      <CollapsibleSection title="Recipe" open onToggle={onToggle}>
        <p>inner</p>
      </CollapsibleSection>
    );
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
  });

  it('drives a real controlled parent through open/close', () => {
    function Wrapper() {
      const [open, setOpen] = useState(false);
      return (
        <CollapsibleSection title="Recipe" open={open} onToggle={setOpen}>
          <p>inner</p>
        </CollapsibleSection>
      );
    }
    render(<Wrapper />);
    const toggle = screen.getByRole('button', { name: /Recipe/ });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');

    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');

    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
  });

  it('hideHeader suppresses the built-in trigger, leaving only the collapse body', () => {
    render(
      <CollapsibleSection title="Details" open onToggle={() => {}} hideHeader>
        <p>inner content</p>
      </CollapsibleSection>
    );
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText('inner content')).not.toBeNull();
  });
});
