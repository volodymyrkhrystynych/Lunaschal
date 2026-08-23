// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
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
});
