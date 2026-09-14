// @vitest-environment jsdom
/**
 * The composer's location control.
 *
 * The point of the component is that the ask is *visible*: nothing is requested
 * until it is pressed, a refusal is shown rather than swallowed, and a fix
 * attached by accident can be taken back.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { LocationButton, useLocationAsk } from './LocationButton';
import * as geo from '../lib/geo';

function Harness({ onCoords }: { onCoords?: (c: unknown) => void }) {
  const ask = useLocationAsk();
  onCoords?.(ask.coords);
  return <LocationButton ask={ask} />;
}

const label = () => screen.getByTestId('location-label').textContent;

beforeEach(() => vi.restoreAllMocks());
afterEach(() => vi.restoreAllMocks());

describe('useLocationAsk / LocationButton', () => {
  it('asks for nothing until it is pressed', () => {
    const spy = vi.spyOn(geo, 'currentPosition');
    render(<Harness />);
    expect(spy).not.toHaveBeenCalled();
    expect(label()).toBe('Add location');
  });

  it('shows the fix once the device answers', async () => {
    vi.spyOn(geo, 'currentPosition').mockResolvedValue({
      latitude: 43.6532,
      longitude: -79.3832,
    });
    render(<Harness />);
    fireEvent.click(screen.getByTestId('location-button'));
    await waitFor(() => expect(label()).toBe('43.6532, -79.3832'));
  });

  it('says so when the device gives nothing back', async () => {
    // currentPosition resolves null on denial, unavailability or timeout — a
    // refused permission must be visible, not indistinguishable from an
    // untouched button.
    vi.spyOn(geo, 'currentPosition').mockResolvedValue(null);
    render(<Harness />);
    fireEvent.click(screen.getByTestId('location-button'));
    await waitFor(() => expect(label()).toBe('Location unavailable'));
  });

  it('retries on a second press after a refusal', async () => {
    const spy = vi
      .spyOn(geo, 'currentPosition')
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce({ latitude: 1, longitude: 2 });
    render(<Harness />);
    fireEvent.click(screen.getByTestId('location-button'));
    await waitFor(() => expect(label()).toBe('Location unavailable'));
    fireEvent.click(screen.getByTestId('location-button'));
    await waitFor(() => expect(label()).toBe('1.0000, 2.0000'));
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it('clears a held fix on a second press, without asking again', async () => {
    const spy = vi
      .spyOn(geo, 'currentPosition')
      .mockResolvedValue({ latitude: 1, longitude: 2 });
    const seen: unknown[] = [];
    render(<Harness onCoords={c => seen.push(c)} />);
    fireEvent.click(screen.getByTestId('location-button'));
    await waitFor(() => expect(label()).toBe('1.0000, 2.0000'));

    fireEvent.click(screen.getByTestId('location-button'));
    await waitFor(() => expect(label()).toBe('Add location'));
    expect(spy).toHaveBeenCalledTimes(1);
    expect(seen[seen.length - 1]).toBeNull();
  });

  it('ignores a press while the device is still being asked', async () => {
    let settle: (c: geo.Coords | null) => void = () => {};
    const spy = vi
      .spyOn(geo, 'currentPosition')
      .mockReturnValue(new Promise(r => (settle = r)));
    render(<Harness />);
    fireEvent.click(screen.getByTestId('location-button'));
    expect(label()).toBe('Locating…');
    fireEvent.click(screen.getByTestId('location-button'));
    expect(spy).toHaveBeenCalledTimes(1);
    settle({ latitude: 1, longitude: 2 });
    await waitFor(() => expect(label()).toBe('1.0000, 2.0000'));
  });
});
