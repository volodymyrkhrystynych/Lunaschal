import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';

export function useCollectionScans() {
  const client = useQueryClient();
  const scans = useQuery({
    queryKey: ['fanfic', 'collections'],
    queryFn: api.fanfic.collections.list,
    refetchInterval: query =>
      query.state.data?.some(s => s.status === 'pending') ? 2000 : false,
  });
  const progress = scans.data
    ?.map(s => `${s.id}:${s.imported}:${s.status}`)
    .join('|');
  useEffect(() => {
    if (progress) client.invalidateQueries({ queryKey: ['fanfic', 'list'] });
  }, [progress, client]);
  return scans;
}
