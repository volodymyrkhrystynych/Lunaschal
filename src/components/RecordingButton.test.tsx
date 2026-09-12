// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { RecordingButton } from './RecordingButton';

describe('RecordingButton', () => {
  it.each(['Record', 'Journal', 'Transcribe'])(
    'keeps %s stoppable when starting becomes unavailable',
    label => {
      const onClick = vi.fn();
      const { rerender } = render(
        <RecordingButton
          status="idle"
          label={label}
          onClick={onClick}
          disabled
        />
      );
      const button = screen.getByRole('button', {
        name: label,
      }) as HTMLButtonElement;
      expect(button.disabled).toBe(true);
      rerender(
        <RecordingButton
          status="recording"
          label={label}
          onClick={onClick}
          disabled
        />
      );
      expect(screen.getByRole('button', { name: 'Stop recording' })).toBe(
        button
      );
      expect(button.disabled).toBe(false);
      expect(button.getAttribute('aria-pressed')).toBe('true');
      expect(button.className).toContain('bg-red-600');
      fireEvent.click(button);
      expect(onClick).toHaveBeenCalledOnce();
    }
  );

  it('shows and disables startup and finalization without remounting', () => {
    const onClick = vi.fn();
    const { rerender } = render(
      <RecordingButton status="idle" starting onClick={onClick} />
    );
    const button = screen.getByRole('button', {
      name: 'Starting…',
    }) as HTMLButtonElement;
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
    rerender(<RecordingButton status="saving" onClick={onClick} />);
    expect(screen.getByRole('button', { name: 'Saving…' })).toBe(button);
    expect(button.disabled).toBe(true);
    rerender(<RecordingButton status="idle" onClick={onClick} />);
    expect(button.disabled).toBe(false);
    expect(button.getAttribute('aria-pressed')).toBe('false');
  });
});
