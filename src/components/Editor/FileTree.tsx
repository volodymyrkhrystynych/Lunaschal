import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type FileEntry } from '../../hooks/api';

interface Props {
  selectedPath: string | null;
  onSelectFile: (path: string) => void;
}

interface NodeProps {
  entry: FileEntry;
  depth: number;
  expandedDirs: Set<string>;
  onToggleDir: (path: string) => void;
  onSelectFile: (path: string) => void;
  selectedPath: string | null;
  onDelete: (entry: FileEntry) => void;
  onRenameStart: (entry: FileEntry) => void;
}

function FileTreeNode({ entry, depth, expandedDirs, onToggleDir, onSelectFile, selectedPath, onDelete, onRenameStart }: NodeProps) {
  const [hovered, setHovered] = useState(false);
  const isExpanded = expandedDirs.has(entry.path);

  const { data: children } = useQuery({
    queryKey: ['files', 'list', entry.path],
    queryFn: () => api.files.list(entry.path),
    enabled: entry.isDir && isExpanded,
  });

  const indent = depth * 12 + 8;

  return (
    <div>
      <div
        className={`flex items-center gap-1 py-0.5 pr-1 cursor-pointer rounded text-sm select-none group ${
          selectedPath === entry.path
            ? 'bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
            : 'text-[var(--color-text)] hover:bg-white/5'
        }`}
        style={{ paddingLeft: indent }}
        onClick={() => entry.isDir ? onToggleDir(entry.path) : onSelectFile(entry.path)}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        <span className="w-3 text-[var(--color-text-muted)] shrink-0 text-xs">
          {entry.isDir ? (isExpanded ? '▾' : '▸') : ''}
        </span>
        <span className="truncate flex-1">{entry.name}</span>
        {hovered && (
          <div className="flex items-center gap-1 shrink-0" onClick={e => e.stopPropagation()}>
            <button
              className="p-0.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)] text-xs"
              title="Rename"
              onClick={() => onRenameStart(entry)}
            >✎</button>
            <button
              className="p-0.5 text-[var(--color-text-muted)] hover:text-red-400 text-xs"
              title="Delete"
              onClick={() => onDelete(entry)}
            >✕</button>
          </div>
        )}
      </div>
      {entry.isDir && isExpanded && children?.map(child => (
        <FileTreeNode
          key={child.path}
          entry={child}
          depth={depth + 1}
          expandedDirs={expandedDirs}
          onToggleDir={onToggleDir}
          onSelectFile={onSelectFile}
          selectedPath={selectedPath}
          onDelete={onDelete}
          onRenameStart={onRenameStart}
        />
      ))}
    </div>
  );
}

export function FileTree({ selectedPath, onSelectFile }: Props) {
  const queryClient = useQueryClient();
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
  const [newFileName, setNewFileName] = useState('');
  const [showNewFile, setShowNewFile] = useState(false);
  const [renamingEntry, setRenamingEntry] = useState<FileEntry | null>(null);
  const [renameValue, setRenameValue] = useState('');

  const { data: rootEntries } = useQuery({
    queryKey: ['files', 'list', ''],
    queryFn: () => api.files.list(),
  });

  const deleteMutation = useMutation({
    mutationFn: (path: string) => api.files.delete(path),
    onSuccess: (_, path) => {
      queryClient.invalidateQueries({ queryKey: ['files', 'list'] });
      if (selectedPath === path) onSelectFile('');
    },
  });

  const renameMutation = useMutation({
    mutationFn: ({ from, to }: { from: string; to: string }) => api.files.rename(from, to),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['files', 'list'] });
      setRenamingEntry(null);
    },
  });

  const createFile = useMutation({
    mutationFn: (name: string) => api.files.write(name, ''),
    onSuccess: (_, name) => {
      queryClient.invalidateQueries({ queryKey: ['files', 'list', ''] });
      setNewFileName('');
      setShowNewFile(false);
      onSelectFile(name);
    },
  });

  const toggleDir = (path: string) => {
    setExpandedDirs(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const handleDelete = (entry: FileEntry) => {
    if (!confirm(`Move "${entry.name}" to trash?`)) return;
    deleteMutation.mutate(entry.path);
  };

  const handleRenameStart = (entry: FileEntry) => {
    setRenamingEntry(entry);
    setRenameValue(entry.name);
  };

  const handleRenameConfirm = () => {
    if (!renamingEntry || !renameValue.trim()) return;
    const dir = renamingEntry.path.includes('/')
      ? renamingEntry.path.substring(0, renamingEntry.path.lastIndexOf('/') + 1)
      : '';
    renameMutation.mutate({ from: renamingEntry.path, to: dir + renameValue.trim() });
  };

  return (
    <div className="flex flex-col h-full">
      <div className="p-2 border-b border-white/10 flex items-center justify-between">
        <span className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">Files</span>
        <button
          onClick={() => setShowNewFile(true)}
          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-1"
          title="New file"
        >+ New</button>
      </div>

      <div className="flex-1 overflow-y-auto py-1">
        {showNewFile && (
          <div className="px-2 py-1">
            <input
              autoFocus
              value={newFileName}
              onChange={e => setNewFileName(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && newFileName.trim()) createFile.mutate(newFileName.trim());
                if (e.key === 'Escape') { setShowNewFile(false); setNewFileName(''); }
              }}
              onBlur={() => { setShowNewFile(false); setNewFileName(''); }}
              placeholder="filename.md"
              className="w-full bg-[var(--color-bg)] border border-[var(--color-primary)] rounded px-2 py-0.5 text-sm text-[var(--color-text)] focus:outline-none"
            />
          </div>
        )}

        {renamingEntry && (
          <div className="px-2 py-1">
            <input
              autoFocus
              value={renameValue}
              onChange={e => setRenameValue(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') handleRenameConfirm();
                if (e.key === 'Escape') setRenamingEntry(null);
              }}
              onBlur={() => setRenamingEntry(null)}
              className="w-full bg-[var(--color-bg)] border border-[var(--color-primary)] rounded px-2 py-0.5 text-sm text-[var(--color-text)] focus:outline-none"
            />
          </div>
        )}

        {rootEntries?.map(entry => (
          <FileTreeNode
            key={entry.path}
            entry={entry}
            depth={0}
            expandedDirs={expandedDirs}
            onToggleDir={toggleDir}
            onSelectFile={onSelectFile}
            selectedPath={selectedPath}
            onDelete={handleDelete}
            onRenameStart={handleRenameStart}
          />
        ))}

        {rootEntries?.length === 0 && !showNewFile && (
          <div className="px-4 py-4 text-xs text-[var(--color-text-muted)]">
            No files yet. Click "+ New" to create one.
          </div>
        )}
      </div>
    </div>
  );
}
