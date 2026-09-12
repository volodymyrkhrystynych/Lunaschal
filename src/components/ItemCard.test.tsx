// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ItemCard } from './ItemCard';

describe('ItemCard', () => {
  it('renders all slots when provided', () => {
    render(
      <ItemCard
        thumbnail={<img alt="cover" src="cover.jpg" />}
        title={<span>Title text</span>}
        titleActions={<button>Edit</button>}
        meta={<span>meta text</span>}
        progress={<div data-testid="progress">50%</div>}
        body={<p>Body content</p>}
        actions={<button>Delete</button>}
      />
    );
    expect(screen.getByAltText('cover')).toBeTruthy();
    expect(screen.getByText('Title text')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Edit' })).toBeTruthy();
    expect(screen.getByText('meta text')).toBeTruthy();
    expect(screen.getByTestId('progress')).toBeTruthy();
    expect(screen.getByText('Body content')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Delete' })).toBeTruthy();
  });

  it('omits optional slots without rendering empty wrapper elements', () => {
    const { container } = render(
      <ItemCard title={<span>Only a title</span>} />
    );
    expect(screen.getByText('Only a title')).toBeTruthy();
    // No header row wrapper should exist when meta/titleActions are absent —
    // the title renders bare, not inside an extra flex/justify-between div.
    expect(container.querySelector('.justify-between')).toBeNull();
    expect(screen.queryAllByRole('button')).toHaveLength(0);
  });

  it('wraps meta alongside the title on a header row when meta is provided', () => {
    const { container } = render(
      <ItemCard title={<span>Title</span>} meta={<span>a meta line</span>} />
    );
    expect(container.querySelector('.justify-between')).not.toBeNull();
    expect(screen.getByText('a meta line')).toBeTruthy();
  });

  it('renders as an <li> when as="li" is passed', () => {
    const { container } = render(
      <ItemCard as="li" title={<span>Row title</span>} />
    );
    expect(container.querySelector('li')).not.toBeNull();
    expect(container.querySelector('div')).toBeNull();
  });

  it('renders as a <div> by default', () => {
    const { container } = render(<ItemCard title={<span>Row title</span>} />);
    expect(container.querySelector('div')).not.toBeNull();
    expect(container.querySelector('li')).toBeNull();
  });

  it('places the thumbnail beside the whole content column, not just the title', () => {
    render(
      <ItemCard
        thumbnail={<img alt="cover" src="cover.jpg" />}
        title={<span>Title</span>}
        body={<p>Body</p>}
      />
    );
    const thumb = screen.getByAltText('cover');
    // The thumbnail's parent should also contain the body text, i.e. it sits
    // beside a column holding title+body, not just title.
    expect(thumb.parentElement?.textContent).toContain('Body');
  });
});
