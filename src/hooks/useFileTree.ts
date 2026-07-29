import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query';
import type { FileEntry } from './api';
import { useShortcutScope } from '../shortcuts/ShortcutProvider';

/** The subset of api.files / api.notebook.files a file tree needs. */
export interface FileTreeApi {
  list: (path?: string) => Promise<FileEntry[]>;
  write: (path: string, content: string) => Promise<{ success: boolean }>;
  rename: (from: string, to: string) => Promise<{ success: boolean }>;
  delete: (path: string) => Promise<{ success: boolean }>;
  mkdir: (path: string) => Promise<{ success: boolean }>;
}

export interface VisibleNode {
  entry: FileEntry;
  depth: number;
}

interface UseFileTreeOptions {
  api: FileTreeApi;
  /** e.g. ['files'] or ['notebook', 'files'] — becomes the react-query key prefix. */
  queryKeyPrefix: string[];
  selectedPath: string | null;
  onSelectFile: (path: string) => void;
  /** Narrows the flattened, depth-first node list (e.g. a search filter)
   * before it drives rendering, keyboard nav, and focus clamping alike. */
  filterVisible?: (nodes: VisibleNode[]) => VisibleNode[];
  /** Shortcut scope depth this tree navigates at. Defaults to 1. */
  scopeDepth?: number;
}

/**
 * Shared state machine for a keyboard-navigable file tree backed by a
 * directory-listing API — used by the Editor file tree and the Notebook
 * tree. Owns expand/collapse, the flattened visible-node list, keyboard
 * focus/drill-in/drill-out, and the create/rename/delete file & folder
 * mutations plus their inline-input UI state.
 */
export function useFileTree({
  api,
  queryKeyPrefix,
  selectedPath,
  onSelectFile,
  filterVisible,
  scopeDepth = 1,
}: UseFileTreeOptions) {
  const queryClient = useQueryClient();
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
  const [newFileName, setNewFileName] = useState('');
  const [showNewFile, setShowNewFile] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [showNewFolder, setShowNewFolder] = useState(false);
  const [renamingEntry, setRenamingEntry] = useState<FileEntry | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [focusedIdx, setFocusedIdx] = useState(0);

  const listKey = useMemo(() => [...queryKeyPrefix, 'list'], [queryKeyPrefix]);

  const dirPaths = useMemo(
    () => ['', ...Array.from(expandedDirs)],
    [expandedDirs]
  );

  const dirQueries = useQueries({
    queries: dirPaths.map(p => ({
      queryKey: [...listKey, p],
      queryFn: () => api.list(p || undefined),
    })),
  });

  // Cheap to rebuild every render; a useMemo here would need a variable-length dep array
  const childrenByDir = new Map<string, FileEntry[]>();
  dirPaths.forEach((p, i) => {
    const data = dirQueries[i].data;
    if (data) childrenByDir.set(p, data);
  });

  const allVisibleNodes: VisibleNode[] = [];
  const walk = (dirPath: string, depth: number) => {
    const children = childrenByDir.get(dirPath);
    if (!children) return;
    for (const entry of children) {
      allVisibleNodes.push({ entry, depth });
      if (entry.isDir && expandedDirs.has(entry.path))
        walk(entry.path, depth + 1);
    }
  };
  walk('', 0);

  const visibleNodes = filterVisible
    ? filterVisible(allVisibleNodes)
    : allVisibleNodes;
  const rootEntries = childrenByDir.get('');

  useEffect(() => {
    setFocusedIdx(i => Math.min(i, Math.max(visibleNodes.length - 1, 0)));
  }, [visibleNodes.length]);

  const deleteMutation = useMutation({
    mutationFn: (path: string) => api.delete(path),
    onSuccess: (_, path) => {
      queryClient.invalidateQueries({ queryKey: listKey });
      if (selectedPath === path) onSelectFile('');
    },
  });

  const renameMutation = useMutation({
    mutationFn: ({ from, to }: { from: string; to: string }) =>
      api.rename(from, to),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: listKey });
      setRenamingEntry(null);
    },
  });

  const createFile = useMutation({
    mutationFn: (name: string) => api.write(name, ''),
    onSuccess: (_, name) => {
      queryClient.invalidateQueries({ queryKey: listKey });
      setNewFileName('');
      setShowNewFile(false);
      onSelectFile(name);
    },
  });

  const createFolder = useMutation({
    mutationFn: (name: string) => api.mkdir(name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: listKey });
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

  useShortcutScope(scopeDepth, {
    next: () =>
      setFocusedIdx(i => Math.min(i + 1, Math.max(visibleNodes.length - 1, 0))),
    prev: () => setFocusedIdx(i => Math.max(i - 1, 0)),
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
    renameMutation.mutate({
      from: renamingEntry.path,
      to: dir + renameValue.trim(),
    });
  };

  return {
    expandedDirs,
    toggleDir,
    visibleNodes,
    rootEntries,
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
  };
}
