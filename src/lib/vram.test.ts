import { describe, it, expect } from 'vitest';
import { vramColors } from './vram';

describe('vramColors', () => {
  it('is red above 90%', () => {
    expect(vramColors(91)).toEqual({ bar: 'bg-red-500', text: 'text-red-400' });
    expect(vramColors(100)).toEqual({
      bar: 'bg-red-500',
      text: 'text-red-400',
    });
  });

  it('is yellow between 70% and 90%', () => {
    expect(vramColors(71)).toEqual({
      bar: 'bg-yellow-500',
      text: 'text-yellow-400',
    });
    expect(vramColors(90)).toEqual({
      bar: 'bg-yellow-500',
      text: 'text-yellow-400',
    });
  });

  it('is green at or below 70%', () => {
    expect(vramColors(70)).toEqual({
      bar: 'bg-green-500',
      text: 'text-green-400',
    });
    expect(vramColors(0)).toEqual({
      bar: 'bg-green-500',
      text: 'text-green-400',
    });
  });
});
