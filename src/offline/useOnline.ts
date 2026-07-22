import { useSyncExternalStore } from 'react';
import { onlineManager } from '@tanstack/react-query';

/**
 * Reactive backend-reachability flag, driven by the same onlineManager the
 * rest of the offline layer uses (see installBackendOnlineManager).
 */
export function useOnline(): boolean {
  return useSyncExternalStore(
    cb => onlineManager.subscribe(cb),
    () => onlineManager.isOnline(),
    () => true // SSR/first paint: assume online
  );
}
