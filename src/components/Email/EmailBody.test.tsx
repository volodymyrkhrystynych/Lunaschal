// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { EmailBody } from './EmailBody';

describe('EmailBody', () => {
  it('falls back to plain text when there is no HTML', () => {
    // Mail synced before body_html existed, and plain-text mail, both land
    // here — the migration deliberately leaves the column empty.
    render(<EmailBody html="" text="just some text" />);
    expect(screen.getByText('just some text')).toBeTruthy();
  });

  it('renders the stored HTML', () => {
    const { container } = render(
      <EmailBody html="<p>Hello <strong>world</strong></p>" text="" />
    );
    expect(container.querySelector('strong')?.textContent).toBe('world');
  });

  it('does not load images until asked', () => {
    const { container } = render(
      <EmailBody
        html='<img data-src="/api/email/images/abc" alt="Logo">'
        text=""
      />
    );
    const img = container.querySelector('img')!;
    // The whole privacy claim: opening an email issues no image request.
    expect(img.getAttribute('src')).toBeNull();
    expect(screen.getByText(/1 image not shown/)).toBeTruthy();
  });

  it('promotes data-src to src when Load images is clicked', () => {
    const { container } = render(
      <EmailBody html='<img data-src="/api/email/images/abc">' text="" />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Load images' }));

    expect(container.querySelector('img')!.getAttribute('src')).toBe(
      '/api/email/images/abc'
    );
  });

  it('refuses to promote a remote data-src', () => {
    // The sanitizer guarantees a local path, but a row stored before that
    // guarantee must not be able to turn into a tracking fetch here.
    const { container } = render(
      <EmailBody
        html='<img data-src="https://track.example/pixel.gif">'
        text=""
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Load images' }));

    expect(container.querySelector('img')!.getAttribute('src')).toBeNull();
  });

  it('resets to images-hidden when a different email is shown', () => {
    const { rerender } = render(
      <EmailBody html='<img data-src="/api/email/images/a">' text="" />
    );
    fireEvent.click(screen.getByRole('button', { name: 'Load images' }));

    // Consenting to one sender's pictures is not consent for the next one's.
    rerender(<EmailBody html='<img data-src="/api/email/images/b">' text="" />);

    expect(screen.getByRole('button', { name: 'Load images' })).toBeTruthy();
  });

  it('shows no images banner when the body has none', () => {
    render(<EmailBody html="<p>text only</p>" text="" />);
    expect(screen.queryByRole('button', { name: 'Load images' })).toBeNull();
  });
});
