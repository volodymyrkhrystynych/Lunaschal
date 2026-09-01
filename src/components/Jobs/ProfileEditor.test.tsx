// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
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
});
