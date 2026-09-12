// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { TagPill } from './TagPill';

describe('TagPill', () => {
  it('renders the label', () => {
    render(<TagPill active={false} onClick={() => {}} label="Recent" />);
    expect(screen.getByText('Recent')).toBeTruthy();
  });

  it('renders a count in parentheses when provided', () => {
    render(
      <TagPill active={false} onClick={() => {}} label="soup" count={3} />
    );
    expect(screen.getByRole('button').textContent).toBe('soup(3)');
  });

  it('renders the # prefix when provided', () => {
    render(
      <TagPill active={false} onClick={() => {}} label="soup" prefix="#" />
    );
    expect(screen.getByRole('button').textContent).toBe('#soup');
  });

  it('omits the prefix and count by default', () => {
    render(<TagPill active={false} onClick={() => {}} label="Recent" />);
    expect(screen.getByRole('button').textContent).toBe('Recent');
  });

  it('calls onClick when clicked', () => {
    const onClick = vi.fn();
    render(<TagPill active={false} onClick={onClick} label="Recent" />);
    fireEvent.click(screen.getByRole('button'));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('applies the active classes when active', () => {
    render(<TagPill active label="Recent" onClick={() => {}} />);
    const button = screen.getByRole('button');
    expect(button.className).toContain('border-[var(--color-primary)]');
    expect(button.className).toContain('text-[var(--color-primary)]');
  });

  it('applies the inactive classes when not active', () => {
    render(<TagPill active={false} label="Recent" onClick={() => {}} />);
    const button = screen.getByRole('button');
    expect(button.className).toContain('border-white/20');
    expect(button.className).toContain('text-[var(--color-text-muted)]');
  });

  it('lets a call site override the active/inactive colors', () => {
    render(
      <TagPill
        active
        label="Unsorted"
        onClick={() => {}}
        activeClassName="border-amber-400/60 bg-amber-400/10 text-amber-300"
      />
    );
    const button = screen.getByRole('button');
    expect(button.className).toContain('border-amber-400/60');
    expect(button.className).not.toContain('border-[var(--color-primary)]');
  });
});
