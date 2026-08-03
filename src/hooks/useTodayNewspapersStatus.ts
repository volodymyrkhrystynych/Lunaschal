import { useQuery } from '@tanstack/react-query';
import { api } from './api';
import { todayISO, hasMissingEditions } from '../lib/newspapers';

// Mounted once at the App level (not inside the Newspapers view) so the check
// runs — and the sidebar can flag a gap — without the user ever opening the
// tab. It's a plain DB read (GET /api/newspapers/frontpages/<date>), not the
// sync/download endpoint, so it never fetches images. `refetchOnReconnect:
// 'always'` ties it to the same backend-reachability signal as the rest of
// the offline layer (see offline/onlineManager.ts): this query is always
// active, so a reconnect after the Pocket 2 comes back on Tailscale re-checks
// immediately rather than waiting for the next staleTime-driven refetch.
export function useTodayNewspapersStatus(enabled: boolean): boolean {
  const today = todayISO();
  const { data } = useQuery({
    queryKey: ['newspapers', today],
    queryFn: () => api.newspapers.getByDate(today),
    refetchOnReconnect: 'always',
    enabled,
  });
  return data ? hasMissingEditions(data) : false;
}
