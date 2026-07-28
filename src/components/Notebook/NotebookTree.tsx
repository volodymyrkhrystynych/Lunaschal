import { useEffect, useState } from 'react';
import { api, type FileEntry } from '../../hooks/api';
import { useShortcuts } from '../../shortcuts/ShortcutProvider';
import { isEditableTarget } from '../../shortcuts/keymap';
import { matchesQuery } from '../../lib/notebookSearch';
import { useFileTree, type VisibleNode } from '../../hooks/useFileTree';
import { FileTreeRow } from '../FileTreeRow';

interface Props {
  selectedPath: string | null;
  onSelectFile: (path: string) => void;
  /** Reports the keyboard-highlighted node so the pane can preview it. */
  onFocusEntry?: (entry: FileEntry | null) => void;
}

export function NotebookTree({
  selectedPath,
  onSelectFile,
  onFocusEntry,
}: Props) {
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const { level } = useShortcuts();

  const {
    visibleNodes,
    rootEntries,
    expandedDirs,
    toggleDir,
    focusedIdx,
    newFileName,
    setNewFileName,
    showNewFile,
    setShowNewFile,
    newFolderName,
    setNewFolderName,
    showNewFolder,
    setShowNewFolder,
    renamingEntry,
    setRenamingEntry,
    renameValue,
    setRenameValue,
    createFile,
    createFolder,
    handleDelete,
    handleRenameStart,
    handleRenameConfirm,
  } = useFileTree({
    api: api.notebook.files,
    queryKeyPrefix: ['notebook', 'files'],
    selectedPath,
    onSelectFile,
    filterVisible: searchOpen
      ? (nodes: VisibleNode[]) =>
          nodes.filter(n => matchesQuery(n.entry.name, searchQuery))
      : undefined,
  });

  // Surface the highlighted node to the parent so the pane can preview it.
  const focusedEntry = visibleNodes[focusedIdx]?.entry ?? null;
  useEffect(() => {
    onFocusEntry?.(focusedEntry);
    // Only re-report when the identity of the highlighted node changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusedEntry?.path, focusedEntry?.isDir]);

  // Vim-style "/" to filter the tree and Space to expand/contract a folder.
  // Local listeners rather than ScopeHandlers/ActionIds: neither is an
  // app-wide convention, both are specific to this Notebook tree.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (level < 1 || isEditableTarget(e.target)) return;
      if (e.key === '/') {
        e.preventDefault();
        setSearchOpen(true);
        return;
      }
      if (e.code === 'Space') {
        const node = visibleNodes[focusedIdx];
        if (node?.entry.isDir) {
          e.preventDefault();
          toggleDir(node.entry.path);
        }
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [level, visibleNodes, focusedIdx, toggleDir]);

  return (
    <div className="flex flex-col h-full">
      <div className="p-2 border-b border-white/10 flex items-center justify-between">
        <span className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
          Notebook
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setShowNewFolder(true)}
            className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-1"
            title="New folder"
          >
            + Folder
          </button>
          <button
            onClick={() => setShowNewFile(true)}
            className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-1"
            title="New file"
          >
            + New
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto py-1">
        {searchOpen && (
          <div className="px-2 py-1">
            <input
              autoFocus
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Escape') {
                  setSearchOpen(false);
                  setSearchQuery('');
                }
              }}
              onBlur={() => {
                setSearchOpen(false);
                setSearchQuery('');
              }}
              placeholder="/ search…"
              className="w-full bg-[var(--color-bg)] border border-[var(--color-primary)] rounded px-2 py-0.5 text-sm text-[var(--color-text)] focus:outline-none"
            />
          </div>
        )}

        {showNewFile && (
          <div className="px-2 py-1">
            <input
              autoFocus
              value={newFileName}
              onChange={e => setNewFileName(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && newFileName.trim())
                  createFile.mutate(newFileName.trim());
                if (e.key === 'Escape') {
                  setShowNewFile(false);
                  setNewFileName('');
                }
              }}
              onBlur={() => {
                setShowNewFile(false);
                setNewFileName('');
              }}
              placeholder="note.md"
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
                if (e.key === 'Enter' && newFolderName.trim())
                  createFolder.mutate(newFolderName.trim());
                if (e.key === 'Escape') {
                  setShowNewFolder(false);
                  setNewFolderName('');
                }
              }}
              onBlur={() => {
                setShowNewFolder(false);
                setNewFolderName('');
              }}
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
            No notes yet. Click "+ New" to create one.
          </div>
        )}
      </div>
    </div>
  );
}
