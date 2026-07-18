import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../hooks/api';

export function CuratedTagsSection() {
  const queryClient = useQueryClient();
  const [newTagName, setNewTagName] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState('');
  const [error, setError] = useState<string | null>(null);

  const { data: tags, refetch } = useQuery({
    queryKey: ['curatedTags'],
    queryFn: api.curatedTags.list,
  });

  useEffect(() => {
    const scanning = tags?.some(t => t.scanProgress && !t.scanProgress.done);
    if (!scanning) return;
    const id = setInterval(() => refetch(), 2000);
    return () => clearInterval(id);
  }, [tags, refetch]);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['curatedTags'] });

  const createTag = useMutation({
    mutationFn: (name: string) => api.curatedTags.create(name),
    onSuccess: () => {
      setNewTagName('');
      setError(null);
      invalidate();
    },
    onError: (e: Error) => setError(e.message),
  });

  const renameTag = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) =>
      api.curatedTags.rename(id, name),
    onSuccess: () => {
      setEditingId(null);
      setEditingName('');
      setError(null);
      invalidate();
    },
    onError: (e: Error) => setError(e.message),
  });

  const deleteTag = useMutation({
    mutationFn: (id: string) => api.curatedTags.delete(id),
    onSuccess: () => invalidate(),
  });

  const handleAdd = () => {
    const name = newTagName.trim();
    if (name) createTag.mutate(name);
  };

  return (
    <section className="mb-8">
      <h2 className="text-lg font-medium text-[var(--color-text)] mb-2">
        Curated Tags
      </h2>
      <p className="text-sm text-[var(--color-text-muted)] mb-4">
        Tags you define here appear as filter buttons in the Journal. When you
        add a tag, the AI scans all existing entries and applies it
        automatically.
      </p>

      <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10 mb-4">
        <div className="flex gap-2">
          <input
            type="text"
            value={newTagName}
            onChange={e => {
              setNewTagName(e.target.value);
              setError(null);
            }}
            onKeyDown={e => {
              if (e.key === 'Enter') handleAdd();
            }}
            placeholder="New tag name..."
            className="flex-1 bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]"
          />
          <button
            onClick={handleAdd}
            disabled={!newTagName.trim() || createTag.isPending}
            className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
          >
            Add
          </button>
        </div>
        {error && <p className="mt-2 text-sm text-red-400">{error}</p>}
      </div>

      {tags && tags.length > 0 ? (
        <div className="space-y-2">
          {tags.map(tag => (
            <div
              key={tag.id}
              className="flex items-center gap-3 p-3 bg-[var(--color-surface)] rounded-lg border border-white/10"
            >
              {editingId === tag.id ? (
                <>
                  <input
                    autoFocus
                    value={editingName}
                    onChange={e => setEditingName(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter')
                        renameTag.mutate({
                          id: tag.id,
                          name: editingName.trim(),
                        });
                      if (e.key === 'Escape') {
                        setEditingId(null);
                        setError(null);
                      }
                    }}
                    className="flex-1 bg-transparent text-[var(--color-text)] border border-white/10 rounded px-2 py-1 focus:outline-none focus:border-[var(--color-primary)]"
                  />
                  <button
                    onClick={() =>
                      renameTag.mutate({ id: tag.id, name: editingName.trim() })
                    }
                    disabled={renameTag.isPending || !editingName.trim()}
                    className="text-sm text-[var(--color-primary)] hover:text-[var(--color-primary)]/80 disabled:opacity-50"
                  >
                    Save
                  </button>
                  <button
                    onClick={() => {
                      setEditingId(null);
                      setError(null);
                    }}
                    className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                  >
                    Cancel
                  </button>
                </>
              ) : (
                <>
                  <span className="flex-1 text-[var(--color-text)] font-medium">
                    #{tag.name}
                  </span>
                  <span className="text-xs text-[var(--color-text-muted)]">
                    {tag.scanProgress && !tag.scanProgress.done
                      ? `Scanning: ${tag.scanProgress.processed} / ${tag.scanProgress.total}`
                      : `${tag.entryCount} ${tag.entryCount === 1 ? 'entry' : 'entries'}`}
                  </span>
                  <button
                    onClick={() => {
                      setEditingId(tag.id);
                      setEditingName(tag.name);
                      setError(null);
                    }}
                    className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                  >
                    Rename
                  </button>
                  <button
                    onClick={() => deleteTag.mutate(tag.id)}
                    disabled={deleteTag.isPending}
                    className="text-sm text-red-400 hover:text-red-300 disabled:opacity-50"
                  >
                    Delete
                  </button>
                </>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div className="text-[var(--color-text-muted)] text-sm text-center py-8">
          No curated tags yet. Add one above.
        </div>
      )}
    </section>
  );
}
