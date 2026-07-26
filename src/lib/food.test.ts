import { describe, it, expect } from 'vitest';
import { mediaKind, ratingStars, foodTitle, parseTags, mapLink } from './food';

describe('mediaKind', () => {
  it('classifies video and image MIME types', () => {
    expect(mediaKind('video/mp4')).toBe('video');
    expect(mediaKind('video/quicktime')).toBe('video');
    expect(mediaKind('image/jpeg')).toBe('image');
    expect(mediaKind('image/heic')).toBe('image');
  });

  it('falls back to image for unknown types', () => {
    expect(mediaKind('application/octet-stream')).toBe('image');
  });
});

describe('ratingStars', () => {
  it('renders filled and empty stars', () => {
    expect(ratingStars(5)).toBe('★★★★★');
    expect(ratingStars(3)).toBe('★★★☆☆');
    expect(ratingStars(1)).toBe('★☆☆☆☆');
  });

  it('returns empty string when unrated', () => {
    expect(ratingStars(null)).toBe('');
    expect(ratingStars(undefined)).toBe('');
    expect(ratingStars(0)).toBe('');
  });

  it('clamps out-of-range ratings', () => {
    expect(ratingStars(9)).toBe('★★★★★');
  });
});

describe('foodTitle', () => {
  it('prefers dish, then place, then notes, then raw', () => {
    expect(foodTitle({ dish: 'Ramen', place: 'Kinton' })).toBe('Ramen');
    expect(foodTitle({ place: 'Kinton', notes: 'good' })).toBe('Kinton');
    expect(foodTitle({ notes: 'tasty', rawContent: 'blah' })).toBe('tasty');
    expect(foodTitle({ rawContent: 'just a note' })).toBe('just a note');
  });

  it('uses the first line and truncates long titles', () => {
    expect(foodTitle({ rawContent: 'line one\nline two' })).toBe('line one');
    expect(foodTitle({ dish: 'x'.repeat(100) }).endsWith('…')).toBe(true);
  });

  it('falls back to "Food" when empty', () => {
    expect(foodTitle({})).toBe('Food');
    expect(foodTitle({ dish: '   ' })).toBe('Food');
  });
});

describe('parseTags', () => {
  it('parses a JSON array and drops non-strings/blank input', () => {
    expect(parseTags('["a","b"]')).toEqual(['a', 'b']);
    expect(parseTags(null)).toEqual([]);
    expect(parseTags('not json')).toEqual([]);
  });
});

describe('mapLink', () => {
  it('builds an OSM link from coordinates', () => {
    expect(mapLink(43.6532, -79.3832)).toContain('mlat=43.6532');
    expect(mapLink(43.6532, -79.3832)).toContain('mlon=-79.3832');
  });

  it('returns null when either coordinate is missing', () => {
    expect(mapLink(null, -79)).toBeNull();
    expect(mapLink(43, null)).toBeNull();
    expect(mapLink(undefined, undefined)).toBeNull();
  });

  it('accepts a zero coordinate', () => {
    expect(mapLink(0, 0)).not.toBeNull();
  });
});
