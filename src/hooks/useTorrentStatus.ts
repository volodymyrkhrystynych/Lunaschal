import { useQuery } from '@tanstack/react-query';
import { api } from './api';

// Mounted at the App level rather than inside the Torrent view, so the sidebar
// can flag a problem without the tab being open — which is the point: a tunnel
// that dropped or a torrent that errored is exactly what you want to notice
// while you are somewhere else in the app.
//
// Deliberately hits /api/torrents/status rather than the list: it returns three
// integers instead of every torrent, and this runs for the whole session.
export function useTorrentStatus(enabled: boolean): boolean {
  const { data } = useQuery({
    queryKey: ['torrent-status'],
    queryFn: api.torrents.status,
    // A minute is plenty for a badge; the view itself polls at 1.5s when open.
    refetchInterval: 60000,
    refetchOnReconnect: 'always',
    // A stopped stack is a normal state the user chose, not something to nag
    // about — only a running stack with something actually wrong gets a badge.
    retry: false,
    enabled,
  });
  if (!data || !data.available) return false;
  return data.errored > 0 || !data.vpnConnected;
}
