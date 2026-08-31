import { describe, expect, it } from 'vitest';
import {
  commuteBand,
  distanceBand,
  distanceLabel,
  DISTANCE_ANCHOR_LABEL,
} from './jobs';

const job = (over: Partial<Parameters<typeof distanceLabel>[0]> = {}) => ({
  remote: false,
  distanceKm: null as number | null,
  distancePrecision: 'city',
  ...over,
});

describe('distanceBand', () => {
  it('bands the GTA the way someone commuting would', () => {
    expect(distanceBand(2)).toBe('near');
    expect(distanceBand(10)).toBe('near');
    expect(distanceBand(24)).toBe('commutable'); // Mississauga
    expect(distanceBand(30)).toBe('commutable');
    expect(distanceBand(60)).toBe('far'); // Hamilton
    expect(distanceBand(94)).toBe('distant'); // Waterloo
    expect(distanceBand(3644)).toBe('distant');
  });

  it('reports an unplaced posting as unknown rather than distant', () => {
    // The bands drive a colour. Painting "we could not read this location"
    // the same red as "San Francisco" would be a claim the data cannot make.
    expect(distanceBand(null)).toBe('unknown');
  });
});

describe('distanceLabel', () => {
  it('names the anchor so the number is not taken on faith', () => {
    expect(distanceLabel(job({ distanceKm: 23.7 }))).toBe(
      `~24 km from ${DISTANCE_ANCHOR_LABEL}`
    );
  });

  it('answers remote before distance, and never as a number', () => {
    // A remote posting has no commute. "0 km" would say something false about
    // a job three subway stops away.
    expect(distanceLabel(job({ remote: true, distanceKm: 0.9 }))).toBe(
      'Remote'
    );
    expect(distanceLabel(job({ remote: true }))).toBe('Remote');
  });

  it('renders nothing at all when the location could not be placed', () => {
    expect(distanceLabel(job({ distanceKm: null }))).toBeNull();
  });

  it('drops the tilde only when the board posted real coordinates', () => {
    expect(
      distanceLabel(job({ distanceKm: 12, distancePrecision: 'exact' }))
    ).toBe(`12 km from ${DISTANCE_ANCHOR_LABEL}`);
    expect(
      distanceLabel(job({ distanceKm: 12, distancePrecision: 'district' }))
    ).toBe(`~12 km from ${DISTANCE_ANCHOR_LABEL}`);
  });

  it('keeps a decimal only where whole kilometres would round to nothing', () => {
    expect(distanceLabel(job({ distanceKm: 0.9 }))).toBe(
      `~0.9 km from ${DISTANCE_ANCHOR_LABEL}`
    );
    expect(distanceLabel(job({ distanceKm: 167.8 }))).toBe(
      `~168 km from ${DISTANCE_ANCHOR_LABEL}`
    );
  });
});

describe('a body that contradicts the board flag', () => {
  // `work_location` exists precisely for "Remote - Canada" that turns out to
  // want two days a week in a Toronto office. The board's structured flag is
  // never overwritten, so the contradiction has to be readable on the card.
  const hybrid = {
    remote: true,
    distanceKm: 23.7,
    distancePrecision: 'inferred',
    workLocation: 'hybrid',
  };

  it('names the mode and shows the commute instead of just "Remote"', () => {
    expect(distanceLabel(hybrid)).toBe(
      `Hybrid · ~24 km from ${DISTANCE_ANCHOR_LABEL}`
    );
    expect(distanceLabel({ ...hybrid, workLocation: 'onsite' })).toBe(
      `On-site · ~24 km from ${DISTANCE_ANCHOR_LABEL}`
    );
  });

  it('still says Remote when the body agrees with the flag', () => {
    expect(distanceLabel({ ...hybrid, workLocation: 'remote' })).toBe('Remote');
    expect(distanceLabel({ ...hybrid, workLocation: 'unclear' })).toBe(
      'Remote'
    );
    expect(distanceLabel({ ...hybrid, workLocation: '' })).toBe('Remote');
  });

  it('falls back to Remote when there is no city to measure', () => {
    expect(distanceLabel({ ...hybrid, distanceKm: null })).toBe('Remote');
  });

  it('colours a contradicted posting by its real distance', () => {
    expect(commuteBand(hybrid)).toBe('commutable');
    // ...but a genuinely remote one is never painted on the commute scale.
    expect(commuteBand({ ...hybrid, workLocation: 'remote' })).toBe('unknown');
  });
});
