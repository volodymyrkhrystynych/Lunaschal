import { useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import type { Fic } from '../../hooks/api';

const pillBase = 'px-3 py-1 text-sm rounded-full border transition-colors';

/** Folder filter pills shown above the library list. */
export function FolderBar({
  folderId,
  onSelect,
}: {
  folderId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState('');
  const queryClient = useQueryClient();
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['fanfic'] });

  const { data: folders } = useQuery({
    queryKey: ['fanfic', 'folders'],
    queryFn: api.fanfic.folders.list,
  });

  const createFolder = useMutation({
    mutationFn: (n: string) => api.fanfic.folders.create(n),
    onSuccess: result => {
      invalidate();
      setCreating(false);
      setName('');
      onSelect(result.id);
    },
  });

  const renameFolder = useMutation({
    mutationFn: ({ id, n }: { id: string; n: string }) =>
      api.fanfic.folders.rename(id, n),
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

  const active = folders?.find(f => f.id === folderId);
  const activeIndex = folders?.findIndex(f => f.id === folderId) ?? -1;

  const moveActive = (dir: -1 | 1) => {
    if (!folders || activeIndex < 0) return;
    const target = activeIndex + dir;
    if (target < 0 || target >= folders.length) return;
    const ids = folders.map(f => f.id);
    [ids[activeIndex], ids[target]] = [ids[target], ids[activeIndex]];
    reorderFolders.mutate(ids);
  };

  const nameInput = (
    onSubmit: (n: string) => void,
    onCancel: () => void,
    placeholder: string
  ) => (
    <input
      value={name}
      autoFocus
      placeholder={placeholder}
      onChange={e => setName(e.target.value)}
      onKeyDown={e => {
        if (e.key === 'Escape') {
          onCancel();
          setName('');
        }
        if (e.key === 'Enter' && name.trim()) onSubmit(name.trim());
      }}
      onBlur={() => {
        onCancel();
        setName('');
      }}
      className="w-32 px-3 py-1 text-sm rounded-full bg-transparent border border-[var(--color-primary)] text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none"
    />
  );

  return (
    <div className="tag-row flex flex-wrap items-center gap-2 mb-4">
      <button
        onClick={() => onSelect(null)}
        className={`${pillBase} ${
          folderId === null
            ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-text)]'
            : 'border-white/15 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
        }`}
      >
        All
      </button>
      <button
        onClick={() => onSelect(folderId === 'recent' ? null : 'recent')}
        title="All fics, sorted only by the latest threadmark's forum post date"
        className={`${pillBase} ${
          folderId === 'recent'
            ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-text)]'
            : 'border-white/15 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
        }`}
      >
        Recent
      </button>
      <button
        onClick={() => onSelect(folderId === 'unsorted' ? null : 'unsorted')}
        title="Show fics not in any folder"
        className={`${pillBase} ${
          folderId === 'unsorted'
            ? 'border-amber-400/60 bg-amber-400/10 text-amber-300'
            : 'border-white/15 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
        }`}
      >
        Unsorted
      </button>
      {folders?.map(f =>
        renaming && f.id === folderId ? (
          <span key={f.id}>
            {nameInput(
              n => renameFolder.mutate({ id: f.id, n }),
              () => setRenaming(false),
              f.name
            )}
          </span>
        ) : (
          <button
            key={f.id}
            onClick={() => onSelect(f.id)}
            className={`${pillBase} ${
              f.id === folderId
                ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-text)]'
                : 'border-white/15 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
            }`}
          >
            {f.name} <span className="opacity-60">{f.ficCount}</span>
          </button>
        )
      )}
      {creating ? (
        nameInput(
          n => createFolder.mutate(n),
          () => setCreating(false),
          'Folder name…'
        )
      ) : (
        <button
          onClick={() => {
            setCreating(true);
            setName('');
          }}
          className={`${pillBase} border-dashed border-white/20 text-[var(--color-text-muted)] hover:text-[var(--color-text)]`}
          title="New folder"
        >
          + folder
        </button>
      )}
      {active && !renaming && (
        <span className="flex gap-1 text-xs text-[var(--color-text-muted)]">
          <button
            onClick={() => moveActive(-1)}
            disabled={activeIndex <= 0 || reorderFolders.isPending}
            className="hover:text-[var(--color-text)] disabled:opacity-30"
            title="Move folder earlier — its fics sort higher in the All view"
          >
            ◀
          </button>
          <button
            onClick={() => moveActive(1)}
            disabled={
              activeIndex >= (folders?.length ?? 0) - 1 ||
              reorderFolders.isPending
            }
            className="hover:text-[var(--color-text)] disabled:opacity-30"
            title="Move folder later — its fics sort lower in the All view"
          >
            ▶
          </button>
          <span>·</span>
          <button
            onClick={() => {
              setRenaming(true);
              setName(active.name);
            }}
            className="hover:text-[var(--color-text)]"
            title="Rename folder"
          >
            rename
          </button>
          <span>·</span>
          <button
            onClick={() => {
              if (
                window.confirm(
                  `Delete folder "${active.name}"? Fics inside are kept.`
                )
              )
                deleteFolder.mutate(active.id);
            }}
            className="hover:text-red-400"
            title="Delete folder"
          >
            delete
          </button>
        </span>
      )}
    </div>
  );
}

/** "Folders ▾" popover on a fic card: check/uncheck folder membership. */
export function FolderPicker({ fic }: { fic: Fic }) {
  const [open, setOpen] = useState(false);
  // Seeded from fic.folderIds when the menu opens, then updated locally as the
  // user checks boxes. Driving the checkboxes off this instead of fic.folderIds
  // directly is what lets the fic list itself stay put — see closeMenu below.
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(
    null
  );
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  const { data: folders } = useQuery({
    queryKey: ['fanfic', 'folders'],
    queryFn: api.fanfic.folders.list,
    enabled: open,
  });

  const toggle = useMutation({
    mutationFn: ({
      folderId,
      member,
    }: {
      folderId: string;
      member: boolean;
    }) =>
      member
        ? api.fanfic.removeFromFolder(fic.id, folderId)
        : api.fanfic.addToFolder(fic.id, folderId),
    // Only the folder pills' counts refresh live. The fic list query is
    // invalidated on close (below), not per checkbox — otherwise a fic
    // being viewed via the Unsorted filter vanishes from under the user
    // after their first click, before they've picked every folder they want.
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['fanfic', 'folders'] }),
  });

  const openMenu = () => {
    setPendingIds(new Set(fic.folderIds ?? []));
    setOpen(true);
  };

  const closeMenu = () => {
    setOpen(false);
    queryClient.invalidateQueries({ queryKey: ['fanfic'] });
  };

  // Positions the menu in the viewport rather than relative to the button, and
  // clamps it inside the screen — the button sits inside a horizontally
  // clipped scroll container (the library list), so an absolutely-positioned
  // menu anchored to it gets cut off on a narrow phone screen instead of
  // simply overflowing where it's visible.
  useLayoutEffect(() => {
    if (!open) {
      setMenuPos(null);
      return;
    }
    const button = buttonRef.current;
    const menu = menuRef.current;
    if (!button || !menu) return;
    const margin = 8;
    const buttonRect = button.getBoundingClientRect();
    const menuWidth = menu.offsetWidth;
    const left = Math.min(
      Math.max(buttonRect.right - menuWidth, margin),
      window.innerWidth - menuWidth - margin
    );
    setMenuPos({ top: buttonRect.bottom + 4, left });
  }, [open, folders]);

  return (
    <span className="relative">
      <button
        ref={buttonRef}
        onClick={() => (open ? closeMenu() : openMenu())}
        className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        title="Add to folders"
      >
        Folders ▾
      </button>
      {open &&
        createPortal(
          <>
            <div className="fixed inset-0 z-40" onClick={closeMenu} />
            <div
              ref={menuRef}
              className="fixed z-50 min-w-40 max-w-[calc(100vw-16px)] p-2 bg-[var(--color-surface)] border border-white/15 rounded-lg shadow-xl"
              style={{
                top: menuPos?.top ?? 0,
                left: menuPos?.left ?? 0,
                visibility: menuPos ? 'visible' : 'hidden',
              }}
            >
              {folders?.length === 0 && (
                <div className="px-2 py-1 text-xs text-[var(--color-text-muted)]">
                  No folders yet — create one above the list.
                </div>
              )}
              {folders?.map(f => {
                const member = pendingIds.has(f.id);
                return (
                  <label
                    key={f.id}
                    className="flex items-center gap-2 px-2 py-1 text-sm text-[var(--color-text)] rounded hover:bg-white/5 cursor-pointer whitespace-nowrap"
                  >
                    <input
                      type="checkbox"
                      checked={member}
                      onChange={() => {
                        setPendingIds(prev => {
                          const next = new Set(prev);
                          if (member) next.delete(f.id);
                          else next.add(f.id);
                          return next;
                        });
                        toggle.mutate({ folderId: f.id, member });
                      }}
                    />
                    {f.name}
                  </label>
                );
              })}
            </div>
          </>,
          document.body
        )}
    </span>
  );
}
