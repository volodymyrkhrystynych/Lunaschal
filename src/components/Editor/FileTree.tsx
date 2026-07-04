import { useEffect, useMemo, useState } from 'react';
import { useQueries, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type FileEntry } from '../../hooks/api';
import { useShortcuts, useShortcutScope } from '../../shortcuts/ShortcutProvider';
import { SyncButton } from './SyncButton';

interface Props {
  selectedPath: string | null;
  onSelectFile: (path: string) => void;
}

interface VisibleNode {
  entry: FileEntry;
  depth: number;
}

interface RowProps {
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

function FileTreeRow({ entry, depth, isExpanded, isSelected, isFocused, onToggleDir, onSelectFile, onDelete, onRenameStart }: RowProps) {
  const [hovered, setHovered] = useState(false);
  const indent = depth * 12 + 8;

  return (
    <div
      ref={(el) => { if (el && isFocused) el.scrollIntoView({ block: 'nearest' }); }}
      className={`flex items-center gap-1 py-0.5 pr-1 cursor-pointer rounded text-sm select-none group ${
        isSelected
          ? 'bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
          : 'text-[var(--color-text)] hover:bg-white/5'
      } ${isFocused ? 'ring-1 ring-[var(--color-primary)]' : ''}`}
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
  );
}

export function FileTree({ selectedPath, onSelectFile }: Props) {
  const queryClient = useQueryClient();
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
  const [newFileName, setNewFileName] = useState('');
  const [showNewFile, setShowNewFile] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [showNewFolder, setShowNewFolder] = useState(false);
  const [renamingEntry, setRenamingEntry] = useState<FileEntry | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [focusedIdx, setFocusedIdx] = useState(0);
  const { level } = useShortcuts();

  const dirPaths = useMemo(() => ['', ...Array.from(expandedDirs)], [expandedDirs]);

  const dirQueries = useQueries({
    queries: dirPaths.map((p) => ({
      queryKey: ['files', 'list', p],
      queryFn: () => api.files.list(p || undefined),
    })),
  });

  // Cheap to rebuild every render; a useMemo here would need a variable-length dep array
  const childrenByDir = new Map<string, FileEntry[]>();
  dirPaths.forEach((p, i) => {
    const data = dirQueries[i].data;
    if (data) childrenByDir.set(p, data);
  });

  const visibleNodes: VisibleNode[] = [];
  const walk = (dirPath: string, depth: number) => {
    const children = childrenByDir.get(dirPath);
    if (!children) return;
    for (const entry of children) {
      visibleNodes.push({ entry, depth });
      if (entry.isDir && expandedDirs.has(entry.path)) walk(entry.path, depth + 1);
    }
  };
  walk('', 0);

  const rootEntries = childrenByDir.get('');

  useEffect(() => {
    setFocusedIdx((i) => Math.min(i, Math.max(visibleNodes.length - 1, 0)));
  }, [visibleNodes.length]);

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
      queryClient.invalidateQueries({ queryKey: ['files', 'list'] });
      setNewFileName('');
      setShowNewFile(false);
      onSelectFile(name);
    },
  });

  const createFolder = useMutation({
    mutationFn: (name: string) => api.files.mkdir(name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['files', 'list'] });
      setNewFolderName('');
      setShowNewFolder(false);
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

  // Directory prefix of the focused node, for prefilling create inputs
  const focusedDirPrefix = () => {
    const node = visibleNodes[focusedIdx];
    if (!node) return '';
    if (node.entry.isDir) return node.entry.path + '/';
    const p = node.entry.path;
    return p.includes('/') ? p.substring(0, p.lastIndexOf('/') + 1) : '';
  };

  useShortcutScope(1, {
    next: () => setFocusedIdx((i) => Math.min(i + 1, Math.max(visibleNodes.length - 1, 0))),
    prev: () => setFocusedIdx((i) => Math.max(i - 1, 0)),
    drillIn: () => {
      const node = visibleNodes[focusedIdx];
      if (!node) return false;
      if (node.entry.isDir) {
        if (!expandedDirs.has(node.entry.path)) {
          toggleDir(node.entry.path);
        } else if (visibleNodes[focusedIdx + 1]?.depth === node.depth + 1) {
          setFocusedIdx(focusedIdx + 1);
        }
      } else {
        onSelectFile(node.entry.path);
      }
      return true;
    },
    drillOut: () => {
      const node = visibleNodes[focusedIdx];
      if (!node) return false;
      if (node.entry.isDir && expandedDirs.has(node.entry.path)) {
        toggleDir(node.entry.path);
        return true;
      }
      if (node.depth > 0) {
        for (let j = focusedIdx - 1; j >= 0; j--) {
          if (visibleNodes[j].depth === node.depth - 1) {
            setFocusedIdx(j);
            return true;
          }
        }
      }
      return false; // at root with nothing to collapse — back to sidebar
    },
    create: () => {
      setNewFileName(focusedDirPrefix());
      setShowNewFile(true);
    },
    createAlt: () => {
      setNewFolderName(focusedDirPrefix());
      setShowNewFolder(true);
    },
  });

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
      <div className="p-2 border-b border-white/10 flex flex-col gap-1.5">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">Files</span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setShowNewFolder(true)}
              className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-1"
              title="New folder"
            >+ Folder</button>
            <button
              onClick={() => setShowNewFile(true)}
              className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-1"
              title="New file"
            >+ New</button>
          </div>
        </div>
        <SyncButton />
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

        {showNewFolder && (
          <div className="px-2 py-1">
            <input
              autoFocus
              value={newFolderName}
              onChange={e => setNewFolderName(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && newFolderName.trim()) createFolder.mutate(newFolderName.trim());
                if (e.key === 'Escape') { setShowNewFolder(false); setNewFolderName(''); }
              }}
              onBlur={() => { setShowNewFolder(false); setNewFolderName(''); }}
              placeholder="folder name"
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

        {visibleNodes.map((node, idx) => (
          <FileTreeRow
            key={node.entry.path}
            entry={node.entry}
            depth={node.depth}
            isExpanded={expandedDirs.has(node.entry.path)}
            isSelected={selectedPath === node.entry.path}
            isFocused={level >= 1 && idx === focusedIdx}
            onToggleDir={toggleDir}
            onSelectFile={onSelectFile}
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
