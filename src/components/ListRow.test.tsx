// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ListRowButton, ListRowShell } from './ListRow';

describe('ListRowButton', () => {
  it('renders as a single clickable button and fires onClick', () => {
    const onClick = vi.fn();
    render(<ListRowButton title="Row title" onClick={onClick} />);

    const button = screen.getByRole('button');
    fireEvent.click(button);

    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('renders leading, title, subtitle and trailing when provided', () => {
    render(
      <ListRowButton
        leading={<span data-testid="leading">📕</span>}
        title={<span>Main title</span>}
        subtitle={<span>Secondary line</span>}
        trailing={<span data-testid="trailing">chip</span>}
        onClick={() => {}}
      />
    );

    expect(screen.getByTestId('leading')).toBeTruthy();
    expect(screen.getByText('Main title')).toBeTruthy();
    expect(screen.getByText('Secondary line')).toBeTruthy();
    expect(screen.getByTestId('trailing')).toBeTruthy();
  });

  it('omits leading/subtitle/trailing cleanly when not provided', () => {
    const { container } = render(
      <ListRowButton title={<span>Just a title</span>} onClick={() => {}} />
    );

    // Only the caller's own title span should exist — no stray wrapper
    // elements for slots that were never passed.
    expect(container.querySelectorAll('span').length).toBe(1);
    expect(screen.getByText('Just a title')).toBeTruthy();
  });

  it('applies selected-state styling via the caller-supplied className', () => {
    render(
      <ListRowButton
        title="Selected row"
        onClick={() => {}}
        className="border-[var(--color-primary)]"
      />
    );
    expect(
      screen
        .getByRole('button')
        .className.includes('border-[var(--color-primary)]')
    ).toBe(true);
  });
});

describe('ListRowShell', () => {
  it('renders as a non-interactive wrapper, not a button', () => {
    render(<ListRowShell title="Todo title" />);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('allows nested buttons to be clicked independently without triggering the row-level handler', () => {
    const onRowClick = vi.fn();
    const onCheckboxClick = vi.fn(e => e.stopPropagation());
    const onDeleteClick = vi.fn(e => e.stopPropagation());

    render(
      <ListRowShell
        onClick={onRowClick}
        leading={<button onClick={onCheckboxClick}>check</button>}
        title="Row body"
        trailing={<button onClick={onDeleteClick}>delete</button>}
      />
    );

    fireEvent.click(screen.getByText('check'));
    expect(onCheckboxClick).toHaveBeenCalledTimes(1);
    expect(onRowClick).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText('delete'));
    expect(onDeleteClick).toHaveBeenCalledTimes(1);
    expect(onRowClick).not.toHaveBeenCalled();
  });

  it('still fires the row-level onClick when the background is clicked', () => {
    const onRowClick = vi.fn();
    render(
      <ListRowShell onClick={onRowClick} title={<span>Row title</span>} />
    );
    fireEvent.click(screen.getByText('Row title'));
    expect(onRowClick).toHaveBeenCalledTimes(1);
  });

  it('renders with no onClick at all as a plain wrapper (unopenable row case)', () => {
    render(<ListRowShell title="Nowhere to go" />);
    expect(screen.getByText('Nowhere to go')).toBeTruthy();
    // No button anywhere in this minimal render.
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('renders leading/title/subtitle/trailing slots when provided and omits them cleanly when not', () => {
    const { rerender } = render(
      <ListRowShell
        leading={<span data-testid="leading">✓</span>}
        title={<span>Title</span>}
        subtitle={<span>Subtitle</span>}
        trailing={<span data-testid="trailing">✕</span>}
      />
    );
    expect(screen.getByTestId('leading')).toBeTruthy();
    expect(screen.getByText('Subtitle')).toBeTruthy();
    expect(screen.getByTestId('trailing')).toBeTruthy();

    rerender(<ListRowShell title={<span>Title only</span>} />);
    expect(screen.queryByTestId('leading')).toBeNull();
    expect(screen.queryByTestId('trailing')).toBeNull();
  });

  it('applies selected-state styling via the caller-supplied className', () => {
    const { container } = render(
      <ListRowShell
        title="Selected"
        className="ring-1 ring-[var(--color-primary)]"
      />
    );
    expect(
      (container.firstChild as HTMLElement).className.includes('ring-1')
    ).toBe(true);
  });
});
