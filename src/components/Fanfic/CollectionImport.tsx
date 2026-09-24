import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useCollectionScans } from './useCollectionScans';
import { api } from '@/hooks/api';
import { scanStatus, siteLabel } from '@/lib/fanfic';
import { SiteLimit } from './SiteLimit';

const choices: Record<string, [string, string][]> = {
  'fanfiction.net': [
    ['all', 'Favorites and follows'],
    ['favorites', 'Favorites'],
    ['follows', 'Follows'],
  ],
  'archiveofourown.org': [
    ['all', 'Bookmarks and work subscriptions'],
    ['bookmarks', 'Bookmarks'],
    ['subscriptions', 'Work subscriptions'],
  ],
  'patreon.com': [['feed', 'Accessible posts from my feed']],
};

export function CollectionImport() {
  const [site, setSite] = useState('fanfiction.net');
  const [collection, setCollection] = useState('all');
  const [username, setUsername] = useState('');
  const client = useQueryClient();
  const scans = useCollectionScans();
  const start = useMutation({
    mutationFn: () =>
      api.fanfic.collections.start(site, collection, username.trim()),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['fanfic'] });
    },
  });
  const inputClass =
    'p-2 rounded bg-[var(--color-bg)] border border-white/15 text-sm';
  return (
    <div className="space-y-3">
      <p className="text-sm text-[var(--color-text-muted)]">
        For FF.net browser downloads, connect the extension to your signed-in
        browser. Direct HTTP and other sites use Settings → Fanfic site cookies.
        Scans queue full stories for download and skip duplicates. You can
        resume a stopped scan. AO3 imports bookmarked works and individual work
        subscriptions. Patreon imports text posts you can access; locked posts
        are skipped.
      </p>
      <div className="flex flex-wrap gap-2">
        <select
          aria-label="Collection site"
          className={inputClass}
          value={site}
          onChange={e => {
            setSite(e.target.value);
            setCollection(choices[e.target.value][0][0]);
            start.reset();
          }}
        >
          {Object.keys(choices).map(s => (
            <option key={s} value={s}>
              {siteLabel(s)}
            </option>
          ))}
        </select>
        <select
          aria-label="Collection"
          className={inputClass}
          value={collection}
          onChange={e => setCollection(e.target.value)}
        >
          {choices[site].map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        {site === 'archiveofourown.org' && (
          <input
            aria-label="AO3 username"
            placeholder="AO3 username"
            className={inputClass}
            value={username}
            onChange={e => setUsername(e.target.value)}
          />
        )}
        <button
          className={inputClass}
          disabled={
            start.isPending ||
            (site === 'archiveofourown.org' && !username.trim())
          }
          onClick={() => start.mutate()}
        >
          Start / resume import
        </button>
      </div>
      {(start.error || scans.error) && (
        <p role="alert" className="text-sm text-red-400">
          {(start.error || scans.error)?.message}
        </p>
      )}
      {site === 'fanfiction.net' && <SiteLimit />}
      <p className="text-xs text-[var(--color-text-muted)]">
        Story text is saved in the reader. Patreon video, audio and attachment
        downloads are not included.
      </p>
      {scans.data?.map(scan => {
        const state = scanStatus(scan);
        return (
          <div key={scan.id} className="text-sm" role="status">
            {siteLabel(scan.site)} · {scan.collection}: {state.label} ·{' '}
            {scan.pages} pages · {scan.imported} queued ·{' '}
            {scan.found - scan.imported} already in library
            {scan.skipped > 0 && ` · ${scan.skipped} locked posts skipped`}
            {scan.error && (
              <p className={state.retrying ? 'text-amber-400' : 'text-red-400'}>
                {scan.error}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
