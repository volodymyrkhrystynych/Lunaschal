// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { currentPosition } from '../../lib/geo';
import { PlacesSection } from './PlacesSection';

vi.mock('../../hooks/api', () => ({
  api: {
    memory: { places: vi.fn(), savePlace: vi.fn(), deletePlace: vi.fn() },
  },
}));
vi.mock('../../lib/geo', () => ({ currentPosition: vi.fn() }));
const home = {
  id: 'home',
  name: 'Home',
  notes: 'Apartment',
  latitude: 43.65,
  longitude: -79.38,
  radiusM: 150,
};
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.memory.places).mockResolvedValue([home]);
  vi.mocked(api.memory.savePlace).mockResolvedValue(home);
  vi.mocked(api.memory.deletePlace).mockResolvedValue({ ok: true });
});
function mount() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <PlacesSection />
    </QueryClientProvider>
  );
}
it('adds a named place using an explicitly requested device location', async () => {
  vi.mocked(currentPosition).mockResolvedValue({
    latitude: 43.6,
    longitude: -79.4,
  });
  mount();
  expect(currentPosition).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Place name'), {
    target: { value: 'Work' },
  });
  fireEvent.change(screen.getByLabelText('Notes'), {
    target: { value: 'Office' },
  });
  fireEvent.click(screen.getByText('Use current location'));
  await waitFor(() =>
    expect(screen.getByLabelText('Latitude')).toHaveProperty('value', '43.6')
  );
  fireEvent.click(screen.getByText('Add place'));
  await waitFor(() =>
    expect(api.memory.savePlace).toHaveBeenCalledWith(
      {
        name: 'Work',
        notes: 'Office',
        latitude: 43.6,
        longitude: -79.4,
        radiusM: 150,
      },
      undefined
    )
  );
});
it('edits saved places and allows deleting them', async () => {
  mount();
  fireEvent.click(await screen.findByText('Edit Home'));
  fireEvent.change(screen.getByLabelText('Notes'), {
    target: { value: 'New apartment' },
  });
  fireEvent.click(screen.getByText('Save place'));
  await waitFor(() =>
    expect(api.memory.savePlace).toHaveBeenCalledWith(
      expect.objectContaining({ notes: 'New apartment' }),
      'home'
    )
  );
  fireEvent.click(screen.getByText('Delete Home'));
  await waitFor(() => expect(api.memory.deletePlace).toHaveBeenCalled());
});
it('can save a name and notes without GPS when location is unavailable', async () => {
  vi.mocked(currentPosition).mockResolvedValue(null);
  mount();
  fireEvent.change(screen.getByLabelText('Place name'), {
    target: { value: 'Library' },
  });
  fireEvent.click(screen.getByText('Use current location'));
  await screen.findByText(/Location unavailable/);
  fireEvent.click(screen.getByText('Add place'));
  await waitFor(() =>
    expect(api.memory.savePlace).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'Library',
        latitude: null,
        longitude: null,
      }),
      undefined
    )
  );
});
