import { defaultShouldDehydrateQuery, type Query } from '@tanstack/react-query';

// Which queries get written into the persisted (IndexedDB) offline cache.
//
// Starts from react-query's default (only successful queries), then removes
// three things we never want rehydrated on reload:
//
//   - ['meetings','active']: an ephemeral recording poll — stale is worthless.
//   - ['practice','next',…]: "which snippet should I drill next", answered
//     against progress that submitting an attempt immediately changes. Restoring
//     one hands back a snippet already drilled, and — because the answer is a
//     whole drill object, not an id — a drill serialized by an older build, whose
//     fields (`explanation`, `mode`) the current UI expects and does not find.
//   - ['auth','status'] that is logged-out: persisting authenticated:false meant
//     a single moment where the cookie wasn't sent (e.g. wrong host on wake) got
//     baked into IndexedDB and rehydrated on every subsequent reload, stranding
//     the user on Login even after the backend/cookie were fine again. Persist
//     the auth status only when it's a known-good `authenticated:true`, so a good
//     session survives reloads but a transient logged-out never sticks.
export function shouldPersistQuery(query: Query): boolean {
  if (!defaultShouldDehydrateQuery(query)) return false;

  const [k0, k1] = query.queryKey as unknown[];

  if (k0 === 'meetings' && k1 === 'active') return false;

  if (k0 === 'practice' && k1 === 'next') return false;

  if (k0 === 'auth' && k1 === 'status') {
    const data = query.state.data as { authenticated?: boolean } | undefined;
    return data?.authenticated === true;
  }

  return true;
}
