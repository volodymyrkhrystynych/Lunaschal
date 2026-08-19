import { useState } from 'react';

interface CollapsibleSectionProps {
  title: string;
  children: React.ReactNode;
  defaultExpanded?: boolean;
}

export function CollapsibleSection({
  title,
  children,
  defaultExpanded = true,
}: CollapsibleSectionProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  return (
    <section className="mb-4">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full text-left"
      >
        <span className="w-4 h-4 shrink-0 text-[var(--color-text-muted)]">
          {expanded ? '▾' : '▸'}
        </span>
        <h2 className="text-lg font-medium text-[var(--color-text)]">
          {title}
        </h2>
      </button>
      <div
        className={`overflow-hidden transition-[max-height] duration-200 ease-in-out ${
          expanded ? 'max-h-[4000px] mt-4' : 'max-h-0'
        }`}
      >
        <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
          {children}
        </div>
      </div>
    </section>
  );
}
