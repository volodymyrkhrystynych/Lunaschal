// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TagsInput, splitTagsInput } from './TagsInput';

describe('splitTagsInput', () => {
  it('splits on commas, trims, and drops empties', () => {
    expect(splitTagsInput('soup, quick,  chicken ,,')).toEqual([
      'soup',
      'quick',
      'chicken',
    ]);
  });

  it('returns an empty array for blank input', () => {
    expect(splitTagsInput('   ')).toEqual([]);
  });
});

describe('TagsInput', () => {
  it('renders the value joined with ", "', () => {
    render(<TagsInput value={['a', 'b']} onChange={() => {}} />);
    expect(screen.getByDisplayValue('a, b')).toBeTruthy();
  });

  it('commits the parsed array on blur', () => {
    const onChange = vi.fn();
    render(<TagsInput value={[]} onChange={onChange} />);
    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: 'x, y' } });
    fireEvent.blur(input);
    expect(onChange).toHaveBeenCalledWith(['x', 'y']);
  });

  it('does not call onChange on blur when nothing changed', () => {
    const onChange = vi.fn();
    render(<TagsInput value={['a']} onChange={onChange} />);
    const input = screen.getByRole('textbox');
    fireEvent.blur(input);
    expect(onChange).not.toHaveBeenCalled();
  });

  it('resyncs the draft when value changes externally', () => {
    const { rerender } = render(
      <TagsInput value={['a']} onChange={() => {}} />
    );
    expect(screen.getByDisplayValue('a')).toBeTruthy();
    rerender(<TagsInput value={['a', 'b']} onChange={() => {}} />);
    expect(screen.getByDisplayValue('a, b')).toBeTruthy();
  });
});
