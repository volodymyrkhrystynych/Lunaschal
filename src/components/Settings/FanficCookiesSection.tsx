import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  api,
  type BookmarkScanProgress,
  type WatchedScanProgress,
} from '../../hooks/api';

/** Shared by both scans — they walk different listings with the same counters.
 *  `noun` names what a page of that listing holds, and `foldered` is the one
 *  number only the bookmark scan has. */
function ScanStatus({
  scan,
  noun,
  foldered,
}: {
  scan: WatchedScanProgress;
  noun: string;
  foldered?: number | null;
}) {
  if (scan.error) {
    return (
      <p className="text-xs text-red-400 mt-2">Scan stopped: {scan.error}</p>
    );
  }
  if (!scan.done) {
    return (
      <p className="text-xs text-[var(--color-text-muted)] mt-2">
        Scanning page {scan.page}
        {scan.lastPage ? ` / ${scan.lastPage}` : ''} · {scan.imported} imported,{' '}
        {scan.alreadyInLibrary} already in library
      </p>
    );
  }
  return (
    <p className="text-xs text-[var(--color-text-muted)] mt-2">
      Last scan: {scan.imported} imported, {scan.alreadyInLibrary} already in
      library ({scan.found} {noun} seen)
      {typeof foldered === 'number' ? `, ${foldered} filed into folders` : ''}
    </p>
  );
}

function FanficCookieRow({
  domain,
  hasCookie,
  updatedAt,
  hasUserAgent,
  watchedScan,
  bookmarkScan,
}: {
  domain: string;
  hasCookie: boolean;
  updatedAt: string | null;
  hasUserAgent: boolean;
  watchedScan?: WatchedScanProgress;
  bookmarkScan?: BookmarkScanProgress;
}) {
  const [value, setValue] = useState('');
  const queryClient = useQueryClient();

  const save = useMutation({
    mutationFn: (cookie: string) => api.fanfic.cookies.put(domain, cookie),
    onSuccess: () => {
      setValue('');
      queryClient.invalidateQueries({ queryKey: ['fanfic', 'cookies'] });
    },
  });

  const scanWatched = useMutation({
    mutationFn: () => api.fanfic.scanWatched(domain),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['fanfic', 'cookies'] }),
  });

  const scanBookmarks = useMutation({
    mutationFn: () => api.fanfic.scanBookmarks(domain),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['fanfic', 'cookies'] }),
  });

  const scanning = watchedScan && !watchedScan.done;
  const scanningBookmarks = bookmarkScan && !bookmarkScan.done;

  return (
    <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-medium text-[var(--color-text)]">{domain}</h3>
        {hasCookie ? (
          <span className="text-xs text-green-400">
            cookie set
            {updatedAt &&
              ` · ${new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(new Date(updatedAt))}`}
            {!hasUserAgent && (
              <span
                className="text-[var(--color-text-muted)]"
                title="No browser User-Agent was captured from this paste — Cloudflare may reject cf_clearance if it was solved by a different User-Agent than the one making requests. Paste 'Copy Request Headers' (not just the cookie) to capture it."
              >
                {' '}
                · default UA
              </span>
            )}
          </span>
        ) : (
          <span className="text-xs text-[var(--color-text-muted)]">
            no cookie
          </span>
        )}
      </div>
      {/* Must be a textarea, not an <input>: a single-line text input
          silently strips newlines from pasted text (HTML's value
          sanitization algorithm), which glues every line of a "Copy
          Request Headers" paste into one string the backend can no
          longer tell Cookie: and User-Agent: apart in. */}
      <textarea
        value={value}
        onChange={e => {
          setValue(e.target.value);
          if (save.isError) save.reset();
        }}
        spellCheck={false}
        autoComplete="off"
        rows={3}
        placeholder={
          hasCookie
            ? 'paste a new cookie (or full request headers) to replace the stored one'
            : 'xf_user=...; xf_session=...; cf_clearance=... — or paste full request headers'
        }
        className="w-full bg-transparent font-mono text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)] resize-y"
      />
      <div className="flex gap-2 mt-2">
        <button
          onClick={() => save.mutate(value.trim())}
          disabled={!value.trim() || save.isPending}
          className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
        >
          Save
        </button>
        {hasCookie && (
          <button
            onClick={() => save.mutate('')}
            disabled={save.isPending}
            className="px-3 py-2 text-sm text-red-400 hover:text-red-300 disabled:opacity-50"
          >
            Clear
          </button>
        )}
      </div>
      {save.isError && (
        <p className="text-xs text-red-400 mt-2">{save.error.message}</p>
      )}
      {!['fanfiction.net', 'archiveofourown.org', 'patreon.com'].includes(
        domain
      ) && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            onClick={() => scanWatched.mutate()}
            disabled={!hasCookie || !!scanning || scanWatched.isPending}
            title={
              hasCookie
                ? 'Walk this site’s watched-threads list and import anything missing from the library'
                : 'Save a cookie above first — scanning needs a logged-in session'
            }
            className="px-3 py-1.5 text-sm rounded border border-white/10 text-[var(--color-text)] hover:bg-white/5 disabled:opacity-50"
          >
            {scanning ? 'Scanning…' : 'Scan watched threads'}
          </button>
          <button
            onClick={() => scanBookmarks.mutate()}
            disabled={
              !hasCookie || !!scanningBookmarks || scanBookmarks.isPending
            }
            title={
              hasCookie
                ? 'Walk this site’s bookmarks and turn each bookmark’s labels into library folders'
                : 'Save a cookie above first — scanning needs a logged-in session'
            }
            className="px-3 py-1.5 text-sm rounded border border-white/10 text-[var(--color-text)] hover:bg-white/5 disabled:opacity-50"
          >
            {scanningBookmarks ? 'Syncing…' : 'Sync bookmark labels'}
          </button>
        </div>
      )}
      {['fanfiction.net', 'archiveofourown.org', 'patreon.com'].includes(
        domain
      ) && (
        <p className="text-xs text-[var(--color-text-muted)] mt-3">
          Bulk import: Library → Import → My collections.
          {domain === 'archiveofourown.org' &&
            ' Bookmarking tags become folders on that scan.'}
        </p>
      )}
      {watchedScan && <ScanStatus scan={watchedScan} noun="watched threads" />}
      {bookmarkScan && (
        <ScanStatus
          scan={bookmarkScan}
          noun="bookmarks"
          foldered={bookmarkScan.foldered}
        />
      )}
    </div>
  );
}

export function FanficCookiesSection() {
  const { data: cookies, refetch } = useQuery({
    queryKey: ['fanfic', 'cookies'],
    queryFn: api.fanfic.cookies.list,
  });

  useEffect(() => {
    const scanning = cookies?.some(
      c =>
        (c.watchedScan && !c.watchedScan.done) ||
        (c.bookmarkScan && !c.bookmarkScan.done)
    );
    if (!scanning) return;
    const id = setInterval(() => refetch(), 2000);
    return () => clearInterval(id);
  }, [cookies, refetch]);

  return (
    <>
      <p className="text-sm text-[var(--color-text-muted)] mb-4">
        Needed for login-gated fics (e.g. Questionable Questing NSFW sections)
        and for sites Cloudflare challenges (e.g. SpaceBattles). Log in to the
        site in your browser, open DevTools (<code>F12</code>) →{' '}
        <strong>Network</strong> tab, reload the page, click the first request
        in the list, then right-click it →{' '}
        <strong>Copy → Copy Request Headers</strong> and paste the whole thing
        below — the <code>Cookie</code> line is extracted automatically, and so
        is the <code>User-Agent</code> line, since Cloudflare only honors{' '}
        <code>cf_clearance</code> when it's replayed with the same User-Agent
        that solved the challenge. The Cookies tab's <strong>Copy All</strong>{' '}
        JSON, a "Copy as cURL" command, or a bare cookie string still work too,
        but won't carry a User-Agent.
      </p>
      <div className="space-y-4">
        {cookies?.map(c => (
          <FanficCookieRow
            key={c.domain}
            domain={c.domain}
            hasCookie={c.hasCookie}
            updatedAt={c.updatedAt}
            hasUserAgent={c.hasUserAgent}
            watchedScan={c.watchedScan}
            bookmarkScan={c.bookmarkScan}
          />
        ))}
      </div>
    </>
  );
}
