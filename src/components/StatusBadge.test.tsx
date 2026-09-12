// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatusBadge } from './StatusBadge';

describe('StatusBadge', () => {
  it('renders the mapped label and color class for the status', () => {
    render(
      <StatusBadge
        status="done"
        labelMap={{ done: 'Done', error: 'Error' }}
        colorMap={{ done: 'text-green-400', error: 'text-red-400' }}
      />
    );
    const badge = screen.getByText('Done');
    expect(badge.className).toContain('text-green-400');
  });

  it('falls back to the raw status when no label is mapped', () => {
    render(<StatusBadge status="mystery" colorMap={{}} />);
    expect(screen.getByText('mystery')).toBeTruthy();
  });

  it('prefers an explicit label over the label map', () => {
    render(
      <StatusBadge
        status="commutable"
        label="12 km"
        labelMap={{ commutable: 'Commutable' }}
        colorMap={{ commutable: 'text-sky-300' }}
      />
    );
    expect(screen.getByText('12 km')).toBeTruthy();
    expect(screen.queryByText('Commutable')).toBeNull();
  });

  it('applies a custom className instead of the default pill shape', () => {
    render(
      <StatusBadge
        status="flag"
        label="Visa required"
        className="px-1.5 py-0.5 rounded text-[11px]"
        colorMap={{}}
      />
    );
    const badge = screen.getByText('Visa required');
    expect(badge.className).toContain('rounded');
    expect(badge.className).not.toContain('rounded-full');
  });
});
