import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import type { Fic } from '../../hooks/api';

const pillBase = 'px-3 py-1 text-sm rounded-full border transition-colors';

/** Folder filter pills shown above the library list. */
export function FolderBar({ folderId, onSelect }: {
  folderId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState('');
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['fanfic'] });

  const { data: folders } = useQuery({
    queryKey: ['fanfic', 'folders'],
    queryFn: api.fanfic.folders.list,
  });

  const createFolder = useMutation({
    mutationFn: (n: string) => api.fanfic.folders.create(n),
    onSuccess: (result) => {
      invalidate();
      setCreating(false);
      setName('');
      onSelect(result.id);
    },
  });

  const renameFolder = useMutation({
    mutationFn: ({ id, n }: { id: string; n: string }) => api.fanfic.folders.rename(id, n),
    onSuccess: () => {
      invalidate();
      setRenaming(false);
      setName('');
    },
  });

  const deleteFolder = useMutation({
    mutationFn: (id: string) => api.fanfic.folders.delete(id),
    onSuccess: () => {
      invalidate();
      onSelect(null);
    },
  });

  const reorderFolders = useMutation({
    mutationFn: (ids: string[]) => api.fanfic.folders.reorder(ids),
    onSuccess: invalidate,
  });

  const active = folders?.find((f) => f.id === folderId);
  const activeIndex = folders?.findIndex((f) => f.id === folderId) ?? -1;

  const moveActive = (dir: -1 | 1) => {
    if (!folders || activeIndex < 0) return;
    const target = activeIndex + dir;
    if (target < 0 || target >= folders.length) return;
    const ids = folders.map((f) => f.id);
    [ids[activeIndex], ids[target]] = [ids[target], ids[activeIndex]];
    reorderFolders.mutate(ids);
  };

  const nameInput = (onSubmit: (n: string) => void, onCancel: () => void, placeholder: string) => (
    <input value={name} autoFocus placeholder={placeholder}
      onChange={(e) => setName(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Escape') { onCancel(); setName(''); }
        if (e.key === 'Enter' && name.trim()) onSubmit(name.trim());
      }}
      onBlur={() => { onCancel(); setName(''); }}
      className="w-32 px-3 py-1 text-sm rounded-full bg-transparent border border-[var(--color-primary)] text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none" />
  );

  return (
    <div className="flex flex-wrap items-center gap-2 mb-4">
      <button onClick={() => onSelect(null)}
        className={`${pillBase} ${folderId === null
          ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-text)]'
          : 'border-white/15 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}>
        All
      </button>
      <button onClick={() => onSelect(folderId === 'unsorted' ? null : 'unsorted')}
        title="Show fics not in any folder"
        className={`${pillBase} ${folderId === 'unsorted'
          ? 'border-amber-400/60 bg-amber-400/10 text-amber-300'
          : 'border-white/15 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}>
        Unsorted
      </button>
      {folders?.map((f) =>
        renaming && f.id === folderId ? (
          <span key={f.id}>
            {nameInput((n) => renameFolder.mutate({ id: f.id, n }), () => setRenaming(false), f.name)}
          </span>
        ) : (
          <button key={f.id} onClick={() => onSelect(f.id)}
            className={`${pillBase} ${f.id === folderId
              ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-text)]'
              : 'border-white/15 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}>
            {f.name} <span className="opacity-60">{f.ficCount}</span>
          </button>
        ))}
      {creating ? (
        nameInput((n) => createFolder.mutate(n), () => setCreating(false), 'Folder name…')
      ) : (
        <button onClick={() => { setCreating(true); setName(''); }}
          className={`${pillBase} border-dashed border-white/20 text-[var(--color-text-muted)] hover:text-[var(--color-text)]`}
          title="New folder">+ folder</button>
      )}
      {active && !renaming && (
        <span className="flex gap-1 text-xs text-[var(--color-text-muted)]">
          <button onClick={() => moveActive(-1)}
            disabled={activeIndex <= 0 || reorderFolders.isPending}
            className="hover:text-[var(--color-text)] disabled:opacity-30"
            title="Move folder earlier — its fics sort higher in the All view">◀</button>
          <button onClick={() => moveActive(1)}
            disabled={activeIndex >= (folders?.length ?? 0) - 1 || reorderFolders.isPending}
            className="hover:text-[var(--color-text)] disabled:opacity-30"
            title="Move folder later — its fics sort lower in the All view">▶</button>
          <span>·</span>
          <button onClick={() => { setRenaming(true); setName(active.name); }}
            className="hover:text-[var(--color-text)]" title="Rename folder">rename</button>
          <span>·</span>
          <button onClick={() => {
            if (window.confirm(`Delete folder "${active.name}"? Fics inside are kept.`))
              deleteFolder.mutate(active.id);
          }}
            className="hover:text-red-400" title="Delete folder">delete</button>
        </span>
      )}
    </div>
  );
}

/** "Folders ▾" popover on a fic card: check/uncheck folder membership. */
export function FolderPicker({ fic }: { fic: Fic }) {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['fanfic'] });

  const { data: folders } = useQuery({
    queryKey: ['fanfic', 'folders'],
    queryFn: api.fanfic.folders.list,
    enabled: open,
  });

  const toggle = useMutation({
    mutationFn: ({ folderId, member }: { folderId: string; member: boolean }) =>
      member
        ? api.fanfic.removeFromFolder(fic.id, folderId)
        : api.fanfic.addToFolder(fic.id, folderId),
    onSuccess: invalidate,
  });

  return (
    <span className="relative">
      <button onClick={() => setOpen(!open)}
        className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        title="Add to folders">Folders ▾</button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-6 z-20 min-w-40 p-2 bg-[var(--color-surface)] border border-white/15 rounded-lg shadow-xl">
            {folders?.length === 0 && (
              <div className="px-2 py-1 text-xs text-[var(--color-text-muted)]">
                No folders yet — create one above the list.
              </div>
            )}
            {folders?.map((f) => {
              const member = fic.folderIds?.includes(f.id) ?? false;
              return (
                <label key={f.id}
                  className="flex items-center gap-2 px-2 py-1 text-sm text-[var(--color-text)] rounded hover:bg-white/5 cursor-pointer whitespace-nowrap">
                  <input type="checkbox" checked={member}
                    onChange={() => toggle.mutate({ folderId: f.id, member })} />
                  {f.name}
                </label>
              );
            })}
          </div>
        </>
      )}
    </span>
  );
}
