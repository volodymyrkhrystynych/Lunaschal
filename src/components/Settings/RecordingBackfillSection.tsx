import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { ErrorBanner } from '../LoadStates';

/**
 * The one-time catch-up for recordings that predate the titling rules.
 *
 * Two backlogs, described in backend/journal/backfill.py: clips that were never
 * described (the audio model was unconfigured, or its alias named something the
 * router could not load), and entries that were never titled at all — the
 * bottom bar's Record button does not ask for a title, so its entries had none
 * until a description or a transcript arrived to trigger one.
 *
 * Polls only while a run is in flight. Idle, this is one cheap query answered by
 * two COUNTs, and there is nothing to watch.
 */
export function RecordingBackfillSection() {
  const queryClient = useQueryClient();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['journal', 'recordingBackfill'],
    queryFn: () => api.journal.recordingBackfill.status(),
    refetchInterval: q => (q.state.data?.progress.running ? 1500 : false),
  });

  const start = useMutation({
    mutationFn: () => api.journal.recordingBackfill.start(),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ['journal', 'recordingBackfill'],
      }),
  });

  if (isLoading) return null;
  if (isError) {
    return <ErrorBanner error={(error as Error).message} />;
  }
  if (!data) return null;

  const { undescribed, untitled, progress } = data;
  const running = progress.running;
  const nothingToDo = undescribed === 0 && untitled === 0;

  return (
    <div className="space-y-3 text-sm">
      <p className="text-[var(--color-text-muted)]">
        Recordings saved before the app started describing and titling them.
        Describing asks the audio model what is in each clip; titling then
        regenerates the entry&rsquo;s title and tags from that description and
        whatever was transcribed.
      </p>

      {nothingToDo && !running ? (
        <p className="text-[var(--color-text-muted)]">
          Nothing to catch up on — every recording has a description and every
          entry carrying one has a title.
        </p>
      ) : (
        <ul className="text-[var(--color-text-muted)] space-y-0.5">
          <li>
            <strong className="text-[var(--color-text)]">{undescribed}</strong>{' '}
            {undescribed === 1 ? 'clip has' : 'clips have'} no description
          </li>
          <li>
            <strong className="text-[var(--color-text)]">{untitled}</strong>{' '}
            {untitled === 1 ? 'entry' : 'entries'} with a recording{' '}
            {untitled === 1 ? 'has' : 'have'} no title
          </li>
        </ul>
      )}

      {running && (
        <div>
          <div className="text-[var(--color-text-muted)] mb-1">
            {progress.phase === 'titling'
              ? 'Writing titles'
              : 'Describing recordings'}
            : {progress.processed ?? 0} / {progress.total ?? 0}
          </div>
          <div className="h-1.5 rounded bg-white/10 overflow-hidden">
            <div
              className="h-full bg-[var(--color-accent)] transition-[width]"
              style={{
                width: `${progress.total ? ((progress.processed ?? 0) / progress.total) * 100 : 0}%`,
              }}
            />
          </div>
        </div>
      )}

      {/* Standing down is not a failure: the model was paused or the lane was
          taken, the remaining rows are untouched, and pressing the button again
          after resuming picks up exactly where this stopped. Saying so is the
          difference between that and "it broke". */}
      {!running && progress.stopped && (
        <p className="text-[var(--color-text-muted)]">
          Stopped early ({progress.stopped}) after describing{' '}
          {progress.described ?? 0} and titling {progress.titled ?? 0}. Resume
          inference and run it again to carry on.
        </p>
      )}

      {!running && progress.phase === 'done' && !progress.stopped && (
        <p className="text-[var(--color-text-muted)]">
          Finished: described {progress.described ?? 0}, titled{' '}
          {progress.titled ?? 0}
          {progress.failed ? `, ${progress.failed} failed` : ''}.
        </p>
      )}

      <button
        onClick={() => start.mutate()}
        disabled={running || start.isPending || nothingToDo}
        className="px-3 py-1.5 rounded bg-[var(--color-accent)] text-white text-sm disabled:opacity-50"
      >
        {running ? 'Running…' : 'Catch up on recordings'}
      </button>

      {start.isError && <ErrorBanner error={(start.error as Error).message} />}
    </div>
  );
}
