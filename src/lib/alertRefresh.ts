import type { AlertRefreshStatus, RefreshAlertsResult } from '../hooks/api';

/** Starts the server's forum-alerts refresh and waits for its summary. The
 * POST only starts it (reading every forum's alerts outlasts a request a
 * phone keeps alive), so the summary comes from polling its status. A press
 * while one is already running waits for that one instead. */
export async function runAlertRefresh(
  start: () => Promise<AlertRefreshStatus>,
  status: () => Promise<AlertRefreshStatus>,
  {
    intervalMs = 2000,
    sleep = (ms: number) => new Promise<void>(r => setTimeout(r, ms)),
  } = {}
): Promise<RefreshAlertsResult> {
  let current = await start();
  while (current.state === 'running') {
    await sleep(intervalMs);
    current = await status();
  }
  if (current.state === 'error')
    throw new Error(current.error || 'Refreshing alerts failed');
  if (current.state !== 'done' || !current.result)
    throw new Error('The server lost the refresh; try again');
  return current.result;
}
