import { useQuery } from '@tanstack/react-query';
import { api } from './api';
import { todayISO, isTodaySelfieMissing } from '../lib/lifestyle';

// Mounted once at the App level (not inside the Lifestyle view) so the check
// runs — and the sidebar can flag a gap — without the user ever opening the
// tab. `limit(1)` is a cheap DB read (the most recent selfie row, no image
// fetch): if that row isn't dated today, today's selfie hasn't been taken.
// `refetchOnReconnect: 'always'` ties it to the same backend-reachability
// signal as the rest of the offline layer (see offline/onlineManager.ts):
// this query is always active, so a reconnect after the Pocket 2 comes back
// on Tailscale re-checks immediately rather than waiting for the next
// staleTime-driven refetch.
export function useTodaySelfieStatus(enabled: boolean): boolean {
  const today = todayISO();
  const { data } = useQuery({
    queryKey: ['lifestyle', 'selfies', 'latest'],
    queryFn: () => api.lifestyle.selfies.list(1),
    refetchOnReconnect: 'always',
    enabled,
  });
  return data ? isTodaySelfieMissing(data, today) : false;
}
