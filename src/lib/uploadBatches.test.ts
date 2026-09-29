import { describe, expect, it } from 'vitest';
import { MAX_FILES_PER_BATCH, uploadBatches } from './uploadBatches';

const files = (sizes: number[]) => sizes.map((size, i) => ({ i, size }));

describe('uploadBatches', () => {
  it('keeps a small upload in one request', () => {
    expect(uploadBatches(files([1, 2, 3]))).toEqual([files([1, 2, 3])]);
  });

  it('returns no batches for no files', () => {
    expect(uploadBatches([])).toEqual([]);
  });

  it('stays under the backend part limit for a large folder', () => {
    const batches = uploadBatches(files(new Array(1234).fill(1)));
    // Two form parts per file, and the backend refuses a body past 1000.
    for (const batch of batches) expect(batch.length * 2).toBeLessThan(1000);
    expect(batches.flat()).toHaveLength(1234);
    expect(batches[0]).toHaveLength(MAX_FILES_PER_BATCH);
  });

  it('splits on bytes, in order', () => {
    const input = files([60, 50, 10, 100]);
    expect(uploadBatches(input, 100, 100)).toEqual([
      [input[0]],
      [input[1], input[2]],
      [input[3]],
    ]);
  });

  it('sends a file larger than the byte bound on its own', () => {
    const input = files([5, 500, 5]);
    expect(uploadBatches(input, 100, 100)).toEqual([
      [input[0]],
      [input[1]],
      [input[2]],
    ]);
  });
});
