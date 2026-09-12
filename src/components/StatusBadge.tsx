/**
 * A status rendered as one pill: `labelMap`/`colorMap` pick the text and
 * classes for `status`, the same status→class/label map shape that
 * Meetings and Jobs/Feed each hand-rolled separately. `label` overrides the
 * map lookup for a caller whose text is computed rather than static (Jobs'
 * commute distance, e.g. "12 km" — the map only supplies the color band).
 */
export function StatusBadge({
  status,
  labelMap,
  colorMap,
  label,
  className = 'shrink-0 px-2 py-0.5 text-xs rounded-full border',
  title,
}: {
  status: string;
  labelMap?: Record<string, string>;
  colorMap?: Record<string, string>;
  /** Overrides `labelMap[status]` when the text isn't itself a fixed label. */
  label?: string;
  className?: string;
  title?: string;
}) {
  const text = label ?? labelMap?.[status] ?? status;
  const colorClass = colorMap?.[status] ?? '';
  return (
    <span title={title} className={`${className} ${colorClass}`.trim()}>
      {text}
    </span>
  );
}
