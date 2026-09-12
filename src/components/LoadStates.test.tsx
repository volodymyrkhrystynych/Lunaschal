// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LoadingState, EmptyState, ErrorBanner } from './LoadStates';

describe('LoadingState', () => {
  it('renders the default label', () => {
    render(<LoadingState />);
    expect(screen.getByText('Loading…')).toBeTruthy();
  });

  it('renders a custom label', () => {
    render(<LoadingState label="Fetching…" />);
    expect(screen.getByText('Fetching…')).toBeTruthy();
  });

  it('renders the inline variant', () => {
    const { container } = render(<LoadingState variant="inline" />);
    expect(container.querySelector('.flex-1')).toBeNull();
  });

  it('renders the panel variant', () => {
    const { container } = render(<LoadingState variant="panel" />);
    expect(container.querySelector('.flex-1')).not.toBeNull();
  });
});

describe('EmptyState', () => {
  it('renders the title', () => {
    render(<EmptyState title="Nothing here yet" />);
    expect(screen.getByText('Nothing here yet')).toBeTruthy();
  });

  it('renders the message when given', () => {
    render(<EmptyState title="Nothing here yet" message="Add one above." />);
    expect(screen.getByText('Add one above.')).toBeTruthy();
  });

  it('omits the action button when not provided', () => {
    render(<EmptyState title="Nothing here yet" />);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('renders the action button and calls onClick when clicked', () => {
    const onClick = vi.fn();
    render(
      <EmptyState
        title="Nothing here yet"
        action={{ label: 'Add one', onClick }}
      />
    );
    const button = screen.getByRole('button', { name: 'Add one' });
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

describe('ErrorBanner', () => {
  it('normalizes an Error instance', () => {
    render(<ErrorBanner error={new Error('Boom')} />);
    expect(screen.getByText('Boom')).toBeTruthy();
  });

  it('normalizes a plain string', () => {
    render(<ErrorBanner error="Something failed" />);
    expect(screen.getByText('Something failed')).toBeTruthy();
  });

  it('normalizes an unknown value into a flat fallback message', () => {
    render(<ErrorBanner error={{ weird: true }} />);
    expect(screen.getByText('Something went wrong.')).toBeTruthy();
  });

  it('defaults to role="alert"', () => {
    render(<ErrorBanner error="failed" />);
    expect(screen.getByRole('alert')).toBeTruthy();
  });

  it('renders role="status" when given', () => {
    render(<ErrorBanner error="failed" role="status" />);
    expect(screen.getByRole('status')).toBeTruthy();
  });

  it('omits the retry button when onRetry is not provided', () => {
    render(<ErrorBanner error="failed" />);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('renders the retry button and calls onRetry when clicked', () => {
    const onRetry = vi.fn();
    render(<ErrorBanner error="failed" onRetry={onRetry} />);
    const button = screen.getByRole('button', { name: 'Retry' });
    fireEvent.click(button);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
