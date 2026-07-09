import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../hooks/api';
import type { Recipe } from '../hooks/api';
import { useShortcuts, useShortcutScope } from '../shortcuts/ShortcutProvider';

const parseTags = (tags: string | null): string[] => {
  if (!tags) return [];
  try {
    const parsed = JSON.parse(tags);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

const splitTagInput = (input: string): string[] =>
  input.split(',').map((t) => t.trim().toLowerCase()).filter(Boolean);

export function Cookbook() {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [editContent, setEditContent] = useState('');
  const [editTags, setEditTags] = useState('');
  const [showNewRecipe, setShowNewRecipe] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [newContent, setNewContent] = useState('');
  const [newTags, setNewTags] = useState('');
  const [showImport, setShowImport] = useState(false);
  const [importText, setImportText] = useState('');
  const [importUrl, setImportUrl] = useState('');
  const [importError, setImportError] = useState<string | null>(null);
  const [selIndex, setSelIndex] = useState(0);
  const queryClient = useQueryClient();
  const { level } = useShortcuts();

  const { data: recipeTags } = useQuery({
    queryKey: ['cookbook', 'tags'],
    queryFn: api.cookbook.tags,
  });

  const { data: recipes, isLoading } = useQuery({
    queryKey: searchQuery
      ? ['cookbook', 'search', searchQuery]
      : ['cookbook', 'list', { tag: selectedTag }],
    queryFn: () =>
      searchQuery
        ? api.cookbook.search(searchQuery)
        : api.cookbook.list({ tag: selectedTag ?? undefined }),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['cookbook'] });
  };

  const createRecipe = useMutation({
    mutationFn: (data: { title: string; content: string; tags?: string[] }) => api.cookbook.create(data),
    onSuccess: () => {
      invalidate();
      setNewTitle('');
      setNewContent('');
      setNewTags('');
      setShowNewRecipe(false);
    },
  });

  const updateRecipe = useMutation({
    mutationFn: ({ id, ...data }: { id: string; title: string; content: string; tags: string[] }) =>
      api.cookbook.update(id, data),
    onSuccess: () => {
      invalidate();
      setEditingId(null);
    },
  });

  const deleteRecipe = useMutation({
    mutationFn: (id: string) => api.cookbook.delete(id),
    onSuccess: invalidate,
  });

  const importRecipe = useMutation({
    mutationFn: (data: { text?: string; url?: string }) => api.cookbook.importRecipe(data),
    onSuccess: (result) => {
      invalidate();
      setImportText('');
      setImportUrl('');
      setImportError(null);
      setShowImport(false);
      setSearchQuery('');
      setSelectedTag(null);
      setExpandedId(result.id);
    },
    onError: (e: Error) => setImportError(e.message),
  });

  useEffect(() => {
    setSelIndex((i) => Math.min(i, Math.max((recipes?.length ?? 1) - 1, 0)));
  }, [recipes]);

  useShortcutScope(1, {
    next: () => setSelIndex((i) => Math.min(i + 1, Math.max((recipes?.length ?? 1) - 1, 0))),
    prev: () => setSelIndex((i) => Math.max(i - 1, 0)),
    create: () => setShowNewRecipe(true),
    drillIn: () => {
      const recipe = recipes?.[selIndex];
      if (!recipe) return false;
      setExpandedId(expandedId === recipe.id ? null : recipe.id);
      return true;
    },
  });

  const startEdit = (recipe: Recipe) => {
    setEditingId(recipe.id);
    setEditTitle(recipe.title);
    setEditContent(recipe.content);
    setEditTags(parseTags(recipe.tags).join(', '));
  };

  const formatDate = (date: string) => new Intl.DateTimeFormat('en-US', {
    year: 'numeric', month: 'short', day: 'numeric',
  }).format(new Date(date));

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold text-[var(--color-text)]">Cookbook</h1>
        <div className="flex gap-2">
          <button onClick={() => { setShowImport(!showImport); setShowNewRecipe(false); }}
            className="px-4 py-2 border border-white/20 text-[var(--color-text)] rounded-lg hover:bg-white/10 transition-colors">
            Import
          </button>
          <button onClick={() => { setShowNewRecipe(!showNewRecipe); setShowImport(false); }}
            className="px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors">
            + New Recipe
          </button>
        </div>
      </div>

      <div className="mb-4">
        <input type="text" value={searchQuery}
          onChange={(e) => { setSearchQuery(e.target.value); setSelectedTag(null); }}
          placeholder="Search recipes..."
          className="w-full bg-[var(--color-surface)] border border-white/10 rounded-lg px-4 py-2 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]" />
        {(recipeTags?.length ?? 0) > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-2">
            {recipeTags?.map((tag) => (
              <button key={tag.name}
                onClick={() => { setSelectedTag(selectedTag === tag.name ? null : tag.name); setSearchQuery(''); }}
                className={`px-3 py-1 text-xs rounded-full border transition-colors ${
                  selectedTag === tag.name
                    ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                    : 'border-white/20 text-[var(--color-text-muted)] hover:border-white/40 hover:text-[var(--color-text)]'
                }`}>
                #{tag.name}<span className="ml-1 opacity-60">({tag.count})</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {showNewRecipe && (
        <div className="mb-4 p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
          <input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} autoFocus
            onKeyDown={(e) => { if (e.key === 'Escape') setShowNewRecipe(false); }}
            placeholder="Recipe title..."
            className="w-full bg-transparent text-[var(--color-text)] font-medium placeholder:text-[var(--color-text-muted)] focus:outline-none border border-white/10 rounded p-2 mb-2" />
          <textarea value={newContent} onChange={(e) => setNewContent(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Escape') setShowNewRecipe(false); }}
            placeholder={'## Ingredients\n- ...\n\n## Instructions\n1. ...'} rows={8}
            className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none border border-white/10 rounded p-2 mb-2" />
          <input value={newTags} onChange={(e) => setNewTags(e.target.value)}
            placeholder="Tags, comma separated (e.g. soup, quick, chicken)"
            className="w-full bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none border border-white/10 rounded p-2" />
          <div className="flex justify-end gap-2 mt-2">
            <button onClick={() => setShowNewRecipe(false)} className="px-3 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Cancel</button>
            <button
              onClick={() => createRecipe.mutate({ title: newTitle.trim(), content: newContent.trim(), tags: splitTagInput(newTags) })}
              disabled={!newTitle.trim() || !newContent.trim() || createRecipe.isPending}
              className="px-3 py-1 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">Save</button>
          </div>
        </div>
      )}

      {showImport && (
        <div className="mb-4 p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
          <textarea value={importText}
            onChange={(e) => { setImportText(e.target.value); if (e.target.value) setImportUrl(''); }}
            onKeyDown={(e) => { if (e.key === 'Escape') setShowImport(false); }}
            placeholder="Paste a recipe from anywhere — the AI will clean it up..." rows={5} autoFocus
            className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none border border-white/10 rounded p-2 mb-2" />
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xs text-[var(--color-text-muted)]">or</span>
            <input value={importUrl}
              onChange={(e) => { setImportUrl(e.target.value); if (e.target.value) setImportText(''); }}
              placeholder="https://... recipe page URL"
              className="flex-1 bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none border border-white/10 rounded p-2" />
          </div>
          {importError && (
            <div className="mb-2 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded text-sm text-red-400">{importError}</div>
          )}
          <div className="flex justify-end gap-2">
            <button onClick={() => { setShowImport(false); setImportError(null); }}
              className="px-3 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Cancel</button>
            <button
              onClick={() => {
                setImportError(null);
                importRecipe.mutate(importText.trim() ? { text: importText.trim() } : { url: importUrl.trim() });
              }}
              disabled={(!importText.trim() && !importUrl.trim()) || importRecipe.isPending}
              className="px-3 py-1 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">
              {importRecipe.isPending ? 'Extracting recipe…' : 'Import'}
            </button>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto space-y-4">
        {isLoading && <div className="text-[var(--color-text-muted)]">Loading...</div>}

        {recipes?.map((recipe, idx) => {
          const tags = parseTags(recipe.tags);
          const expanded = expandedId === recipe.id || editingId === recipe.id;
          return (
            <div key={recipe.id}
              ref={(el) => { if (el && level >= 1 && idx === selIndex) el.scrollIntoView({ block: 'nearest' }); }}
              className={`p-4 bg-[var(--color-surface)] rounded-lg border ${
                level >= 1 && idx === selIndex ? 'border-[var(--color-primary)]' : 'border-white/10'
              }`}>
              <div className="flex items-start justify-between gap-2 mb-1">
                <button onClick={() => setExpandedId(expanded ? null : recipe.id)}
                  className="text-left text-base font-bold text-[var(--color-text)] hover:text-[var(--color-primary)] transition-colors">
                  {recipe.title}
                </button>
                <div className="flex gap-2 shrink-0">
                  <button onClick={() => startEdit(recipe)}
                    className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Edit</button>
                  <button onClick={() => deleteRecipe.mutate(recipe.id)}
                    className="text-sm text-red-400 hover:text-red-300">Delete</button>
                </div>
              </div>
              <div className="flex items-center gap-2 text-sm text-[var(--color-text-muted)] mb-2">
                <span>{formatDate(recipe.createdAt)}</span>
                {recipe.sourceUrl && (
                  <a href={recipe.sourceUrl} target="_blank" rel="noreferrer"
                    className="underline hover:text-[var(--color-text)] truncate">source</a>
                )}
              </div>

              {editingId === recipe.id ? (
                <div>
                  <input value={editTitle} onChange={(e) => setEditTitle(e.target.value)}
                    placeholder="Recipe title..."
                    className="w-full bg-transparent text-[var(--color-text)] font-medium focus:outline-none border border-white/10 rounded p-2 mb-2" />
                  <textarea value={editContent} onChange={(e) => setEditContent(e.target.value)} rows={10}
                    className="w-full bg-transparent text-[var(--color-text)] resize-none focus:outline-none border border-white/10 rounded p-2 mb-2" />
                  <input value={editTags} onChange={(e) => setEditTags(e.target.value)}
                    placeholder="Tags, comma separated"
                    className="w-full bg-transparent text-sm text-[var(--color-text)] focus:outline-none border border-white/10 rounded p-2" />
                  <div className="flex justify-end gap-2 mt-2">
                    <button onClick={() => setEditingId(null)} className="px-3 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Cancel</button>
                    <button
                      onClick={() => updateRecipe.mutate({ id: recipe.id, title: editTitle.trim(), content: editContent, tags: splitTagInput(editTags) })}
                      disabled={!editTitle.trim() || updateRecipe.isPending}
                      className="px-3 py-1 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">Save</button>
                  </div>
                </div>
              ) : (
                <div className={`text-[var(--color-text)] whitespace-pre-wrap ${expanded ? '' : 'line-clamp-3'}`}>
                  {recipe.content}
                </div>
              )}

              {tags.length > 0 && editingId !== recipe.id && (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {tags.map((tag) => (
                    <span key={tag} className="px-2 py-0.5 text-xs rounded border border-[var(--color-primary)]/40 text-[var(--color-primary)] bg-[var(--color-primary)]/10">{tag}</span>
                  ))}
                </div>
              )}
            </div>
          );
        })}

        {recipes?.length === 0 && !isLoading && (
          <div className="text-center text-[var(--color-text-muted)] py-12">
            {searchQuery ? 'No recipes found' : 'No recipes yet. Add one by hand or import from a page!'}
          </div>
        )}
      </div>
    </div>
  );
}
