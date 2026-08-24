// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type WeatherHour, type WeatherToday } from '@/hooks/api';
import { currentPosition } from '@/lib/geo';
import { WeatherCard } from './WeatherCard';

vi.mock('@/hooks/api', () => ({
  api: {
    lifestyle: {
      weather: {
        today: vi.fn(),
        updateLocation: vi.fn(),
      },
    },
  },
}));

vi.mock('@/lib/geo', () => ({
  currentPosition: vi.fn(),
}));

const today = vi.mocked(api.lifestyle.weather.today);
const updateLocation = vi.mocked(api.lifestyle.weather.updateLocation);
const mockCurrentPosition = vi.mocked(currentPosition);

const hour = (
  hourTs: string,
  overrides: Partial<WeatherHour> = {}
): WeatherHour => ({
  id: hourTs,
  dayKey: '2026-08-17',
  hourTs,
  weatherCode: 0,
  temperatureC: 21,
  wetBulbC: 15,
  humidityPct: 55,
  isActual: false,
  latitude: 43.6532,
  longitude: -79.3832,
  locationSource: 'geolocation',
  ...overrides,
});

const EMPTY: WeatherToday = {
  hours: [],
  location: null,
  sunriseTs: null,
  sunsetTs: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  today.mockResolvedValue(EMPTY);
  mockCurrentPosition.mockResolvedValue(null);
});

function renderCard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <WeatherCard />
    </QueryClientProvider>
  );
}

describe('geolocation on mount', () => {
  it('posts a resolved fix to updateLocation', async () => {
    mockCurrentPosition.mockResolvedValue({ latitude: 1, longitude: 2 });
    updateLocation.mockResolvedValue(EMPTY);
    renderCard();
    await waitFor(() => expect(updateLocation).toHaveBeenCalledWith(1, 2));
  });

  it('skips updateLocation without crashing when geolocation resolves null', async () => {
    mockCurrentPosition.mockResolvedValue(null);
    renderCard();
    await waitFor(() => expect(today).toHaveBeenCalled());
    expect(updateLocation).not.toHaveBeenCalled();
  });
});

describe('rendering', () => {
  it('prompts to set a default location when none is known', async () => {
    today.mockResolvedValue(EMPTY);
    renderCard();
    expect(
      await screen.findByText(/set a default location in settings/i)
    ).toBeTruthy();
  });

  it('shows the current hour condition and temperature', async () => {
    // Both hours are safely in the past regardless of the real system clock,
    // so currentHourIndex deterministically lands on the later (second) one.
    today.mockResolvedValue({
      hours: [
        hour('2020-01-01T10:00:00', { isActual: true }),
        hour('2020-01-01T11:00:00', { temperatureC: 25, isActual: true }),
      ],
      location: { latitude: 1, longitude: 2, source: 'geolocation' },
      sunriseTs: null,
      sunsetTs: null,
    });
    renderCard();
    expect(await screen.findByText(/25°C/)).toBeTruthy();
  });
});

describe('sunrise/sunset', () => {
  it('renders the sunrise/sunset row when both are known', async () => {
    today.mockResolvedValue({
      hours: [hour('2020-01-01T10:00:00', { isActual: true })],
      location: { latitude: 1, longitude: 2, source: 'geolocation' },
      sunriseTs: '2020-01-01T06:30:00',
      sunsetTs: '2020-01-01T20:15:00',
    });
    renderCard();
    expect(await screen.findByText(/🌅/)).toBeTruthy();
    expect(await screen.findByText(/🌇/)).toBeTruthy();
  });

  it('omits the sunrise/sunset row when unknown', async () => {
    today.mockResolvedValue({
      hours: [hour('2020-01-01T10:00:00', { isActual: true })],
      location: { latitude: 1, longitude: 2, source: 'geolocation' },
      sunriseTs: null,
      sunsetTs: null,
    });
    renderCard();
    await screen.findByText(/21°C/);
    expect(screen.queryByText(/🌅/)).toBeNull();
  });
});

describe('night icons', () => {
  it('shows the real moon phase instead of the sun icon at night', async () => {
    // Bracket "now" with a sunrise 6h in the future so isNight is true
    // without needing to fake the system clock — the component's internal
    // moonPhase() (which defaults to `new Date()`) then resolves to the
    // real current phase, computed identically here for the assertion.
    const now = Date.now();
    const iso = (ms: number) => new Date(ms).toISOString();

    today.mockResolvedValue({
      hours: [hour(iso(now - 1000), { weatherCode: 0, isActual: true })],
      location: { latitude: 1, longitude: 2, source: 'geolocation' },
      sunriseTs: iso(now + 6 * 3600_000),
      sunsetTs: iso(now + 18 * 3600_000),
    });
    renderCard();

    const { moonPhase } = await import('@/lib/weather');
    const expectedEmoji = moonPhase(new Date()).emoji;
    // The same icon renders in both the current-condition summary and the
    // (single-hour) strip cell, so there are two matches.
    expect(await screen.findAllByText(expectedEmoji)).toHaveLength(2);
    expect(screen.queryByText('☀️')).toBeNull();
  });
});
