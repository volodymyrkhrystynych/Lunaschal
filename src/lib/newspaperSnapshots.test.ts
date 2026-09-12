import { describe, it, expect } from 'vitest';
import { fromWire, type IssueMarkup } from './newspaperMarkup';
import { pagesNeedingSnapshot, type RenderedPage } from './newspaperSnapshots';

const markupOf = (...pages: number[]): IssueMarkup =>
  fromWire(
    pages.map(page => ({
      page,
      tool: 'pen',
      points: [
        [0.1, 0.2],
        [0.3, 0.4],
      ],
    }))
  );

const have = (entries: [number, number | null][]): Map<number, RenderedPage> =>
  new Map(entries.map(([page, strokes]) => [page, { strokes }]));

describe('pagesNeedingSnapshot', () => {
  it('wants the cover even when nothing has been written on', () => {
    expect(pagesNeedingSnapshot(markupOf(), new Map(), 8)).toEqual([1]);
  });

  it('wants every marked page, in page order', () => {
    expect(pagesNeedingSnapshot(markupOf(5, 3), new Map(), 8)).toEqual([
      1, 3, 5,
    ]);
  });

  it('leaves alone what the server already has', () => {
    const rendered = have([
      [1, null],
      [3, null],
    ]);
    expect(pagesNeedingSnapshot(markupOf(3, 5), rendered, 8)).toEqual([5]);
  });

  it('re-renders a page this session has drawn on since uploading it', () => {
    const rendered = have([
      [1, 0],
      [3, 1],
    ]);
    // Page 3 now carries two strokes; page 1 is unchanged at none.
    expect(pagesNeedingSnapshot(markupOf(3, 3), rendered, 8)).toEqual([3]);
  });

  it('drops a page whose ink was erased, rather than uploading a blank', () => {
    // Queued when it had ink, then rubbed clean before its turn came round.
    const rendered = have([[1, 0]]);
    expect(pagesNeedingSnapshot(markupOf(), rendered, 8)).toEqual([]);
  });

  it('ignores pages outside the issue', () => {
    expect(pagesNeedingSnapshot(markupOf(99), new Map(), 4)).toEqual([1]);
  });

  it('wants nothing from an issue with no pages', () => {
    expect(pagesNeedingSnapshot(markupOf(), new Map(), 0)).toEqual([]);
  });
});
