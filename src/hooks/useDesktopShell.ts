import { useEffect, useState } from 'react';
import { hasDesktopBridge } from '../lib/desktopBridge';

export function useDesktopShell(): boolean {
  const [isDesktopShell, setIsDesktopShell] = useState(hasDesktopBridge);

  useEffect(() => {
    const detect = () => setIsDesktopShell(hasDesktopBridge());
    window.addEventListener('pywebviewready', detect);
    detect();
    return () => window.removeEventListener('pywebviewready', detect);
  }, []);

  return isDesktopShell;
}
