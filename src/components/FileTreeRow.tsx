import { useState } from 'react';
import type { FileEntry } from '../hooks/api';

interface Props {
  entry: FileEntry;
  depth: number;
  isExpanded: boolean;
  isSelected: boolean;
  isFocused: boolean;
  onToggleDir: (path: string) => void;
  onSelectFile: (path: string) => void;
  onDelete: (entry: FileEntry) => void;
  onRenameStart: (entry: FileEntry) => void;
}

/** One row of a file tree (currently just Editor's FileTree) — name + expand
 * caret, with hover-revealed rename/delete buttons. */
export function FileTreeRow({
  entry,
  depth,
  isExpanded,
  isSelected,
  isFocused,
  onToggleDir,
  onSelectFile,
  onDelete,
  onRenameStart,
}: Props) {
  const [hovered, setHovered] = useState(false);
  const indent = depth * 12 + 8;

  return (
    <div
      ref={el => {
        if (el && isFocused) el.scrollIntoView({ block: 'nearest' });
      }}
      className={`flex items-center gap-1 py-0.5 pr-1 cursor-pointer rounded text-sm select-none group ${
        isSelected
          ? 'bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
          : 'text-[var(--color-text)] hover:bg-white/5'
      } ${isFocused ? 'ring-1 ring-[var(--color-primary)]' : ''}`}
      style={{ paddingLeft: indent }}
      onClick={() =>
        entry.isDir ? onToggleDir(entry.path) : onSelectFile(entry.path)
      }
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <span className="w-3 text-[var(--color-text-muted)] shrink-0 text-xs">
        {entry.isDir ? (isExpanded ? '▾' : '▸') : ''}
      </span>
      <span className="truncate flex-1">{entry.name}</span>
      {hovered && (
        <div
          className="flex items-center gap-1 shrink-0"
          onClick={e => e.stopPropagation()}
        >
          <button
            className="p-0.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)] text-xs"
            title="Rename"
            onClick={() => onRenameStart(entry)}
          >
            ✎
          </button>
          <button
            className="p-0.5 text-[var(--color-text-muted)] hover:text-red-400 text-xs"
            title="Delete"
            onClick={() => onDelete(entry)}
          >
            ✕
          </button>
        </div>
      )}
    </div>
  );
}
