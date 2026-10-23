// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { BriefingContent } from './BriefingContent';
import { splitServerOddities } from '@/lib/serverOddities';

const content =
  'Your morning plan.\n\n**Server oddities (past day)**\n- **New** · lunaschal-deploy · 120× — `Failed to start`\n  The deployment timed out.\n- **Ongoing** · lunaschal · 2× — `Error`\n- …and 3 more.\n_The server journal could not be read._';
const metadata = '{"briefing":true}';

describe('briefing oddities', () => {
  it('keeps the saved report, counts and notices without needing a server', () => {
    const report = splitServerOddities(content, metadata)!;
    expect(report.prose).toBe('Your morning plan.');
    expect(report.items).toHaveLength(2);
    expect(report.items[0].title).toContain('120×');
    expect(report.items[0].note).toBe('The deployment timed out.');
    expect(report.notices).toContain('3 more');
    expect(report.notices).toContain('journal could not be read');
  });
  it('leaves ordinary replies, malformed metadata and quiet reports alone', () => {
    for (const meta of [null, '{', '{}', '{"briefing":false}']) {
      expect(splitServerOddities(content, meta)).toBeNull();
    }
    expect(
      splitServerOddities(
        '**Server oddities:** none in the past day.',
        metadata
      )
    ).toBeNull();
  });
  it('opens a dialog, expands an entry, and closes with focus restored', () => {
    render(<BriefingContent content={content} metadata={metadata} />);
    expect(screen.getByText('Your morning plan.')).toBeTruthy();
    expect(screen.queryByText('The deployment timed out.')).toBeNull();
    const trigger = screen.getByRole('button', { name: /View details/ });
    fireEvent.click(trigger);
    expect(screen.getByRole('dialog')).toBeTruthy();
    const details = screen.getByText('Failed to start').closest('details')!;
    fireEvent.click(details.querySelector('summary')!);
    expect(details.open).toBe(true);
    expect(screen.getByText('The deployment timed out.')).toBeTruthy();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(trigger);
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
