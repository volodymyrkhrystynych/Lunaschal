// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConfirmDialog } from './ConfirmDialog';

describe('ConfirmDialog', () => {
  it('renders title and message when open', () => {
    render(
      <ConfirmDialog
        open
        title="Delete this thing?"
        message="This cannot be undone."
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    expect(screen.getByText('Delete this thing?')).toBeTruthy();
    expect(screen.getByText('This cannot be undone.')).toBeTruthy();
  });

  it('renders nothing when not open', () => {
    render(
      <ConfirmDialog
        open={false}
        title="Delete this thing?"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    expect(screen.queryByText('Delete this thing?')).toBeNull();
  });

  it('calls onConfirm when the confirm button is clicked', () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Delete this thing?"
        confirmLabel="Delete"
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />
    );
    fireEvent.click(screen.getByText('Delete'));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('calls onCancel when the cancel button is clicked', () => {
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Delete this thing?"
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />
    );
    fireEvent.click(screen.getByText('Cancel'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('calls onCancel when the backdrop is clicked', () => {
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Delete this thing?"
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />
    );
    fireEvent.click(screen.getByRole('dialog'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('does not cancel when clicking inside the panel', () => {
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Delete this thing?"
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />
    );
    fireEvent.click(screen.getByText('Delete this thing?'));
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('applies destructive styling when danger is set', () => {
    render(
      <ConfirmDialog
        open
        title="Delete this thing?"
        danger
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    const confirmButton = screen.getByText('Delete');
    expect(confirmButton.className).toMatch(/red-/);
  });

  it('does not apply destructive styling by default', () => {
    render(
      <ConfirmDialog
        open
        title="Do the thing?"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    const confirmButton = screen.getByText('Confirm');
    expect(confirmButton.className).not.toMatch(/red-/);
  });
});
