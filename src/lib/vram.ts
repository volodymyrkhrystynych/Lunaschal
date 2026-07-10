// Shared color thresholds for VRAM usage bars/labels (Settings page's full
// budget breakdown and the compact live tracker next to the record button).
export function vramColors(pct: number): { bar: string; text: string } {
  if (pct > 90) return { bar: 'bg-red-500', text: 'text-red-400' };
  if (pct > 70) return { bar: 'bg-yellow-500', text: 'text-yellow-400' };
  return { bar: 'bg-green-500', text: 'text-green-400' };
}
