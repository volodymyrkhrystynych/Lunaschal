// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ImageLightbox } from './ImageLightbox';

describe('ImageLightbox', () => {
  it('renders nothing when src is null', () => {
    const { container } = render(
      <ImageLightbox src={null} onClose={() => {}} />
    );
    expect(container.innerHTML).toBe('');
  });

  it('renders the image with default empty alt text', () => {
    const { container } = render(
      <ImageLightbox src="/foo.jpg" onClose={() => {}} />
    );
    const img = container.querySelector('img')!;
    expect(img.getAttribute('src')).toBe('/foo.jpg');
    expect(img.getAttribute('alt')).toBe('');
  });

  it('renders a custom alt when provided', () => {
    render(<ImageLightbox src="/foo.jpg" onClose={() => {}} alt="A sketch" />);
    expect(screen.getByAltText('A sketch')).toBeDefined();
  });

  it('applies a white background only when whiteBg is set', () => {
    const { container, rerender } = render(
      <ImageLightbox src="/foo.jpg" onClose={() => {}} />
    );
    expect(container.querySelector('img')!.className).not.toContain('bg-white');

    rerender(<ImageLightbox src="/foo.jpg" onClose={() => {}} whiteBg />);
    expect(container.querySelector('img')!.className).toContain('bg-white');
  });

  it('calls onClose when the backdrop is clicked', () => {
    const onClose = vi.fn();
    const { container } = render(
      <ImageLightbox src="/foo.jpg" onClose={onClose} />
    );
    fireEvent.click(container.querySelector('img')!.parentElement!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when the close button is clicked', () => {
    const onClose = vi.fn();
    render(<ImageLightbox src="/foo.jpg" onClose={onClose} />);
    fireEvent.click(screen.getByRole('button'));
    expect(onClose).toHaveBeenCalled();
  });

  it('does not stop the image click from reaching the backdrop close handler', () => {
    // The image itself calls stopPropagation, so clicking it directly must
    // not trigger onClose even though the backdrop wraps it.
    const onClose = vi.fn();
    const { container } = render(
      <ImageLightbox src="/foo.jpg" onClose={onClose} />
    );
    fireEvent.click(container.querySelector('img')!);
    expect(onClose).not.toHaveBeenCalled();
  });
});
