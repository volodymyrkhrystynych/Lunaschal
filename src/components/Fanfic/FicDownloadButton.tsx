import { useQuery } from '@tanstack/react-query';
import { api, type FicChapterSummary } from '../../hooks/api';
import { useOnline } from '../../offline/useOnline';
import { useFicDownload } from './useFicDownload';

/**
 * "Save this whole fic for offline" — prefetches every chapter's content into
 * the persisted cache so the fic is fully readable without the backend.
 *
 * Only meaningful for a remote thin client (network mode): on the machine that
 * *is* the server every chapter is always reachable, so the count would just be
 * confusing noise. `networkMode` comes from the already-cached auth status.
 */
export function FicDownloadButton({
  chapters,
}: {
  chapters?: FicChapterSummary[];
}) {
  const online = useOnline();
  const { data: auth } = useQuery({
    queryKey: ['auth', 'status'],
    queryFn: api.auth.status,
  });
  const { status, done, total, start } = useFicDownload(chapters);

  // Hide unless we're an over-the-network client; also hides while auth status
  // is still loading, so it never flashes on the server.
  if (!auth?.networkMode) return null;
  if (total === 0) return null;

  const complete = done >= total;
  let label: string;
  let disabled = false;

  if (status === 'downloading') {
    label = `Saving… ${done}/${total}`;
    disabled = true;
  } else if (complete) {
    label = `✓ Saved offline (${total})`;
    disabled = true;
  } else if (!online) {
    label = "Offline — can't save";
    disabled = true;
  } else if (status === 'error') {
    label = `⤓ Retry (${done}/${total} saved)`;
  } else {
    label = `⤓ Save for offline (${done}/${total})`;
  }

  return (
    <button
      onClick={() => void start()}
      disabled={disabled}
      title="Download every chapter so this fic reads offline"
      className="mt-2 w-full text-xs px-2 py-1.5 rounded border border-white/15 text-[var(--color-text-muted)] hover:bg-white/10 disabled:opacity-60 disabled:hover:bg-transparent transition-colors"
    >
      {label}
    </button>
  );
}
