// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, type JobProfileBundle } from '@/hooks/api';
import { ProfileEditor } from './ProfileEditor';

afterEach(() => vi.restoreAllMocks());

describe('ProfileEditor', () => {
  it('renders a cached profile from before list preferences existed', async () => {
    const profile = {
      fullName: 'Ada Lovelace',
      email: '',
      phone: '',
      location: '',
      headline: '',
      summary: '',
      workAuthorization: '',
      salaryExpectation: '',
      noticePeriod: '',
      availabilityDate: '',
      relocationWillingness: '',
      securityClearance: '',
      eeoAnswers: '',
      avoidClearanceRoles: false,
      softSalaryFloor: null,
      softPreferences: '',
      // links and companyBlacklist are intentionally absent: persisted query
      // caches created before those fields existed still reach this component.
    } as JobProfileBundle['profile'];
    vi.spyOn(api.jobs.profile, 'get').mockResolvedValue({
      profile,
      roles: [],
      skills: [],
      education: [],
      answers: [],
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={client}>
        <ProfileEditor />
      </QueryClientProvider>
    );

    expect(await screen.findByDisplayValue('Ada Lovelace')).toBeTruthy();
    expect(
      (
        screen.getByLabelText(
          'Explicit company blacklist (comma-separated)'
        ) as HTMLInputElement
      ).value
    ).toBe('');
  });

  it('shows a value that arrives after the first render', async () => {
    // The persisted query cache renders immediately on reload, so the first
    // paint is stale data and the real profile lands a moment later. A Field
    // that seeds `useState(value)` and never resyncs shows the stale value
    // forever — which is a saved setting appearing blank, and worse, a blur
    // then writes the stale value back over the real one.
    const base = {
      fullName: '',
      email: '',
      phone: '',
      location: '',
      headline: '',
      summary: '',
      workAuthorization: '',
      salaryExpectation: '',
      noticePeriod: '',
      availabilityDate: '',
      relocationWillingness: '',
      securityClearance: '',
      eeoAnswers: '',
      avoidClearanceRoles: false,
      softSalaryFloor: null,
      softPreferences: '',
      links: [],
      companyBlacklist: [],
    } as unknown as JobProfileBundle['profile'];

    const bundle = (maxDistanceKm: number | null) =>
      ({
        profile: { ...base, maxDistanceKm },
        roles: [],
        skills: [],
        education: [],
        answers: [],
      }) as unknown as JobProfileBundle;

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    // Stale persisted cache: the radius was never stored when this was written.
    client.setQueryData(['jobs', 'profile'], bundle(null));
    // The refetch that follows returns what the server actually has.
    vi.spyOn(api.jobs.profile, 'get').mockResolvedValue(bundle(200));

    render(
      <QueryClientProvider client={client}>
        <ProfileEditor />
      </QueryClientProvider>
    );

    const input = () =>
      screen.getByLabelText(
        'Max commute (km from Union Station)'
      ) as HTMLInputElement;

    expect(input().value).toBe('');
    await waitFor(() => expect(input().value).toBe('200'));
  });
});
