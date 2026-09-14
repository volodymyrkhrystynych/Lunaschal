import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';

export function SiteLimit() {
  const client = useQueryClient();
  const status = useQuery({
    queryKey: ['fanfic', 'site-limit'],
    queryFn: api.fanfic.collections.limit,
    refetchInterval: 5000,
  });
  const resume = useMutation({
    mutationFn: api.fanfic.collections.resume,
    onSuccess: () => client.invalidateQueries({ queryKey: ['fanfic'] }),
  });
  const s = status.data;
  if (!s || (!s.paused && s.cooldownUntil * 1000 <= Date.now())) return null;
  return (
    <div
      role="status"
      className="mb-3 rounded border border-amber-500/30 p-3 text-sm"
    >
      <p>
        {s.paused
          ? 'FF.net downloads paused: browser verification required.'
          : 'FF.net downloads cooling down.'}
      </p>
      {s.cooldownUntil * 1000 > Date.now() && (
        <p>
          Next attempt after {new Date(s.cooldownUntil * 1000).toLocaleString()}
          .
        </p>
      )}
      <p>
        Queued stories and saved chapters are retained. Other sites can
        continue.
      </p>
      {s.paused && (
        <>
          <p>
            Open FF.net in Firefox and complete any challenge. Refresh your
            saved site cookies if needed.
          </p>
          <button
            className="mt-2 rounded border px-3 py-1"
            disabled={resume.isPending}
            onClick={() => resume.mutate()}
          >
            Resume FF.net downloads
          </button>
        </>
      )}
      {resume.error && <p role="alert">{resume.error.message}</p>}
    </div>
  );
}
