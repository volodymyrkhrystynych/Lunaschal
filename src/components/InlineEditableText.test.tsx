// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { useState } from 'react';
import { InlineEditableText } from './InlineEditableText';

/** A thin controlled wrapper mirroring how a real call site owns `editing`. */
function Harness({
  onSave,
  initialValue = 'Original title',
  draftKey,
  initialEditing = false,
}: {
  onSave: (v: string) => void;
  initialValue?: string;
  draftKey?: string;
  /** Mimics a caller (e.g. `DailyTasks`/`TodoRow`) that persists the
   *  `editing` flag itself, so a remount after a killed tab comes back
   *  already in edit mode rather than reaching it via a click. */
  initialEditing?: boolean;
}) {
  const [editing, setEditing] = useState(initialEditing);
  const [value, setValue] = useState(initialValue);
  return (
    <InlineEditableText
      value={value}
      editing={editing}
      onStartEdit={() => setEditing(true)}
      onStopEdit={() => setEditing(false)}
      onSave={v => {
        setValue(v);
        setEditing(false);
        onSave(v);
      }}
      draftKey={draftKey}
    />
  );
}

describe('InlineEditableText', () => {
  it('renders the display value', () => {
    render(<Harness onSave={vi.fn()} />);
    expect(screen.getByText('Original title')).not.toBeNull();
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('entering edit mode shows an input with the current value, focused', () => {
    render(<Harness onSave={vi.fn()} />);
    fireEvent.click(screen.getByText('Original title'));

    const input = screen.getByRole('textbox') as HTMLInputElement;
    expect(input.value).toBe('Original title');
    expect(document.activeElement).toBe(input);
  });

  it('blur saves the new value via onSave', () => {
    const onSave = vi.fn();
    render(<Harness onSave={onSave} />);
    fireEvent.click(screen.getByText('Original title'));

    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: 'Renamed' } });
    fireEvent.blur(input);

    expect(onSave).toHaveBeenCalledWith('Renamed');
    expect(screen.getByText('Renamed')).not.toBeNull();
  });

  it('Enter saves', () => {
    const onSave = vi.fn();
    render(<Harness onSave={onSave} />);
    fireEvent.click(screen.getByText('Original title'));

    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: 'Via enter' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(onSave).toHaveBeenCalledWith('Via enter');
    expect(screen.getByText('Via enter')).not.toBeNull();
  });

  it('Escape cancels without calling onSave and reverts the displayed value', () => {
    const onSave = vi.fn();
    render(<Harness onSave={onSave} />);
    fireEvent.click(screen.getByText('Original title'));

    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: 'Abandoned edit' } });
    fireEvent.keyDown(input, { key: 'Escape' });

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText('Original title')).not.toBeNull();
    expect(screen.queryByText('Abandoned edit')).toBeNull();
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('trims and does not save an empty value, closing instead', () => {
    const onSave = vi.fn();
    render(<Harness onSave={onSave} />);
    fireEvent.click(screen.getByText('Original title'));

    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.blur(input);

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText('Original title')).not.toBeNull();
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('with a draftKey, an in-progress edit survives a killed-and-reopened tab', () => {
    const draftKey = 'test:edit-title';
    const { unmount } = render(
      <Harness onSave={vi.fn()} draftKey={draftKey} />
    );
    fireEvent.click(screen.getByText('Original title'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'Half-typed te' },
    });

    // The tab is killed mid-edit: no blur/Enter/Escape ever fires, so
    // nothing here saves or closes the field before it disappears.
    unmount();

    // Reopening is a fresh mount (a new component instance) that comes
    // back already in edit mode — the same way `DailyTasks`/`TodoRow`
    // restore their own persisted `editing` flag on reload — so the
    // recovered draft must be visible with no click needed.
    render(<Harness onSave={vi.fn()} draftKey={draftKey} initialEditing />);

    expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe(
      'Half-typed te'
    );
  });

  it('without a draftKey, an in-progress edit does not survive a killed-and-reopened tab', () => {
    const { unmount } = render(<Harness onSave={vi.fn()} />);
    fireEvent.click(screen.getByText('Original title'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'Half-typed te' },
    });

    unmount();

    render(<Harness onSave={vi.fn()} initialEditing />);

    expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe(
      'Original title'
    );
  });

  it('re-entering edit after a save seeds the input from the latest value', () => {
    const onSave = vi.fn();
    render(<Harness onSave={onSave} />);
    fireEvent.click(screen.getByText('Original title'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'First save' },
    });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });

    fireEvent.click(screen.getByText('First save'));
    expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe(
      'First save'
    );
  });
});
