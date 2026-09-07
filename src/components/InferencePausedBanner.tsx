import { useQuery } from '@tanstack/react-query';
import { api } from '../hooks/api';

/** A slim strip across the app while GPU inference is switched off.
 *
 * Settings owns the control, but the *consequence* is felt everywhere else —
 * a chat message that refuses, a journal entry that stays unpolished. Without
 * this, the only way to find out why is to remember you turned it off, and a
 * pause is measured in hours.
 *
 * No polling interval: this is a state a person changes deliberately, so
 * react-query's refetch-on-focus is enough and every view does not need a
 * request every few seconds. It shares the Settings panel's query key, so the
 * two are one request while both are mounted.
 */
export function InferencePausedBanner() {
  const { data } = useQuery({
    queryKey: ['settings', 'inference'],
    queryFn: api.settings.inference,
    staleTime: 30_000,
  });

  if (!data?.paused) return null;

  const queued = data.queueDepth ?? 0;

  return (
    <div
      role="status"
      className="shrink-0 px-3 py-1.5 text-xs bg-amber-500/15 text-amber-200 border-t border-amber-500/30 flex items-center gap-2"
    >
      <span
        className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0"
        aria-hidden="true"
      />
      <span>
        GPU inference is paused.
        {queued > 0 &&
          ` ${queued} ${queued === 1 ? 'job is' : 'jobs are'} waiting.`}{' '}
        Transcription and photo reading still work.
      </span>
    </div>
  );
}
