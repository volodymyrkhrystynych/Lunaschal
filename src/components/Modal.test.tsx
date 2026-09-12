// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Modal } from './Modal';

describe('Modal', () => {
  it('renders children inside the panel', () => {
    render(
      <Modal onClose={() => {}}>
        <div>panel content</div>
      </Modal>
    );
    expect(screen.getByText('panel content')).toBeTruthy();
  });

  it('calls onClose when the backdrop is clicked', () => {
    const onClose = vi.fn();
    render(
      <Modal onClose={onClose} ariaLabel="test modal">
        <div>content</div>
      </Modal>
    );
    fireEvent.click(screen.getByLabelText('test modal'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does not close when the panel itself is clicked', () => {
    const onClose = vi.fn();
    render(
      <Modal onClose={onClose} className="panel">
        <div>content</div>
      </Modal>
    );
    fireEvent.click(screen.getByText('content'));
    expect(onClose).not.toHaveBeenCalled();
  });

  it('respects closeOnBackdropClick=false', () => {
    const onClose = vi.fn();
    render(
      <Modal onClose={onClose} closeOnBackdropClick={false} ariaLabel="menu">
        <div>content</div>
      </Modal>
    );
    fireEvent.click(screen.getByLabelText('menu'));
    expect(onClose).not.toHaveBeenCalled();
  });

  it('closes on Escape by default', () => {
    const onClose = vi.fn();
    render(
      <Modal onClose={onClose}>
        <div>content</div>
      </Modal>
    );
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does not close on Escape when closeOnEscape=false', () => {
    const onClose = vi.fn();
    render(
      <Modal onClose={onClose} closeOnEscape={false}>
        <div>content</div>
      </Modal>
    );
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
  });
});
