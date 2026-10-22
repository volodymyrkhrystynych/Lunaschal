// @vitest-environment jsdom
/**
 * Home and Work are dated roles over saved places: setting one is a period
 * with a start date, and the history of earlier ones stays visible.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { PlacesSection } from './PlacesSection';
import { localDayKey } from '../../lib/dates';

vi.mock('../../hooks/api', () => ({
  api: {
    memory: {
      places: vi.fn(),
      savePlace: vi.fn(),
      deletePlace: vi.fn(),
      placeRoles: vi.fn(),
      assignRole: vi.fn(),
      updateRole: vi.fn(),
      deleteRole: vi.fn(),
    },
  },
}));

const memory = vi.mocked(api.memory);

const place = (id: string, name: string, located = true) => ({
  id,
  name,
  notes: '',
  latitude: located ? 43.6 : null,
  longitude: located ? -79.4 : null,
  radiusM: 150,
});

const renderSection = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <PlacesSection />
    </QueryClientProvider>
  );

beforeEach(() => {
  vi.clearAllMocks();
  memory.places.mockResolvedValue([
    place('flat', 'Old flat'),
    place('home', 'New flat'),
    place('nowhere', 'Somewhere', false),
  ]);
  memory.placeRoles.mockResolvedValue([
    {
      id: 'r2',
      role: 'home',
      placeId: 'home',
      placeName: 'New flat',
      latitude: 43.6,
      longitude: -79.4,
      radiusM: 150,
      startedOn: '2026-03-01',
      endedOn: null,
    },
    {
      id: 'r1',
      role: 'home',
      placeId: 'flat',
      placeName: 'Old flat',
      latitude: 43.6,
      longitude: -79.4,
      radiusM: 150,
      startedOn: '2024-01-01',
      endedOn: '2026-03-01',
    },
  ]);
});

describe('PlacesSection roles', () => {
  it('marks the current Home and lists the earlier one in history', async () => {
    renderSection();
    expect(await screen.findByText('Home since 2026-03-01')).toBeTruthy();
    const history = screen.getByRole('region', { name: 'Home history' });
    expect(history.textContent).toContain('Old flat');
    expect(history.textContent).toContain('New flat');
  });

  it('offers only located places and assigns a role from a date', async () => {
    memory.assignRole.mockResolvedValue({} as never);
    renderSection();
    const form = await screen.findByRole('form', { name: 'Set Home or Work' });
    const select = form.querySelector('select') as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBe(3));
    expect([...select.options].map(o => o.text)).not.toContain('Somewhere');

    fireEvent.change(select, { target: { value: 'flat' } });
    fireEvent.change(screen.getByLabelText('Role'), {
      target: { value: 'work' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add as Work' }));
    await waitFor(() =>
      expect(memory.assignRole).toHaveBeenCalledWith(
        'work',
        'flat',
        localDayKey()
      )
    );
  });

  it('commits an edited end date and removes a period', async () => {
    memory.updateRole.mockResolvedValue({} as never);
    memory.deleteRole.mockResolvedValue({ ok: true });
    renderSection();
    const until = await screen.findByLabelText('Old flat until');
    fireEvent.change(until, { target: { value: '2026-02-01' } });
    fireEvent.blur(until);
    await waitFor(() =>
      expect(memory.updateRole).toHaveBeenCalledWith('r1', {
        endedOn: '2026-02-01',
      })
    );
    const remove = screen.getByRole('button', {
      name: 'Remove Old flat period',
    }) as HTMLButtonElement;
    await waitFor(() => expect(remove.disabled).toBe(false));
    fireEvent.click(remove);
    await waitFor(() => expect(memory.deleteRole).toHaveBeenCalled());
    expect(memory.deleteRole.mock.calls[0][0]).toBe('r1');
  });
});
