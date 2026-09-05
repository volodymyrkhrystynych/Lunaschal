// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PaperImageWarnings } from './PaperImageWarnings';
import type { PageImage } from '@/lib/paperImages';

const image = (over: Partial<PageImage> = {}): PageImage => ({
  id: 'img-1',
  url: '',
  x: 100,
  y: 100,
  width: 400,
  height: 200,
  rotation: 0,
  flipped: false,
  locked: false,
  position: 0,
  ...over,
});

const SCALE = 0.5;

describe('PaperImageWarnings', () => {
  it('marks the picture the server refused, and only that one', () => {
    // The banner above the page says a picture in this paper never made it.
    // Which one is a question only the page can answer: a refused picture is
    // drawn from the blob it was pasted from, exactly like a saved one.
    render(
      <PaperImageWarnings
        images={[image(), image({ id: 'img-2', url: '/api/paper/x' })]}
        failures={{ 'img-1': 'unsupported image type: image/heic' }}
        scale={SCALE}
      />
    );

    expect(screen.getByTestId('paper-image-warning-img-1')).toBeTruthy();
    expect(screen.queryByTestId('paper-image-warning-img-2')).toBeNull();
  });

  it('says what went wrong, so the mark is worth more than a red dot', () => {
    render(
      <PaperImageWarnings
        images={[image()]}
        failures={{ 'img-1': 'unsupported image type: image/heic' }}
        scale={SCALE}
      />
    );

    const badge = screen.getByTestId('paper-image-warning-img-1');
    expect(badge.getAttribute('aria-label')).toContain(
      'never reached the server'
    );
    expect(badge.getAttribute('title')).toContain('unsupported image type');
  });

  it('sits on the picture, in the same scaled page space the canvas draws in', () => {
    render(
      <PaperImageWarnings
        images={[image()]}
        failures={{ 'img-1': 'nope' }}
        scale={SCALE}
      />
    );

    // Centre of a 400x200 box at (100,100) is (300,200); at half scale, (150,
    // 100), less half the 32px badge.
    const badge = screen.getByTestId('paper-image-warning-img-1');
    expect(badge.style.left).toBe(`${150 - 16}px`);
    expect(badge.style.top).toBe(`${100 - 16}px`);
  });

  it('is never mirrored by the picture it marks', () => {
    // The outline follows the picture's own rotation and flip; the badge is a
    // sibling of it, not a child, or a flipped photo would carry a mirrored ⚠.
    const { container } = render(
      <PaperImageWarnings
        images={[image({ rotation: 90, flipped: true })]}
        failures={{ 'img-1': 'nope' }}
        scale={SCALE}
      />
    );

    const badge = screen.getByTestId('paper-image-warning-img-1');
    expect(badge.style.transform).toBe('');
    const outline = container.querySelector('.border-dashed') as HTMLElement;
    expect(outline.style.transform).toBe('rotate(90deg) scaleX(-1)');
  });

  it('never takes a pointer — an overlay over the canvas must not eat a stroke', () => {
    render(
      <PaperImageWarnings
        images={[image()]}
        failures={{ 'img-1': 'nope' }}
        scale={SCALE}
      />
    );

    expect(screen.getByTestId('paper-image-warnings').className).toContain(
      'pointer-events-none'
    );
  });

  it('renders nothing at all when every picture is fine', () => {
    // A picture merely waiting for the next Save is the ordinary state of every
    // pasted picture, and must not be warned about.
    render(
      <PaperImageWarnings images={[image()]} failures={{}} scale={SCALE} />
    );

    expect(screen.queryByTestId('paper-image-warnings')).toBeNull();
  });
});
