import { useQuery } from '@tanstack/react-query';
import { api } from './api';
import { todayISO, isTodayCaloriesLow } from '../lib/lifestyle';

// Mounted once at the App level (not inside the Lifestyle view), same
// reasoning as useTodaySelfieStatus: the sidebar can flag a low-calorie day
// without the user ever opening the tab. `refetchOnReconnect: 'always'` ties
// it to the same backend-reachability signal as the rest of the offline
// layer (see offline/onlineManager.ts).
export function useTodayCaloriesStatus(enabled: boolean): boolean {
  const today = todayISO();
  const { data } = useQuery({
    queryKey: ['lifestyle', 'calories', 'day', today],
    queryFn: () => api.lifestyle.calories.day(today),
    refetchOnReconnect: 'always',
    enabled,
  });
  return data ? isTodayCaloriesLow(data.total) : false;
}
