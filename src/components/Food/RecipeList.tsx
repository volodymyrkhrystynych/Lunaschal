import { useEffect, useRef, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import type { Recipe, RecipeMedia } from '../../hooks/api';
import { useShortcutScope } from '../../shortcuts/ShortcutProvider';
import { useListSelection } from '../../shortcuts/useListSelection';
import { useRecorder } from '../../hooks/useRecorder';
import { parseTags, mediaKind } from '../../lib/food';
import { MessageMarkdown } from '../MessageMarkdown';

const splitTagInput = (input: string): string[] =>
  input
    .split(',')
    .map(t => t.trim().toLowerCase())
    .filter(Boolean);

interface PickedMedia {
  file: File;
  url: string;
  kind: 'image' | 'video';
}

/** Photo/video thumbnails — read-only gallery for the view state, matching
 * FoodLog.tsx's card gallery so photos read the same way across the tab. */
function MediaGallery({ media }: { media: RecipeMedia[] }) {
  if (media.length === 0) return null;
  return (
    <div className="flex gap-2 overflow-x-auto mb-3 pb-1">
      {media.map(m =>
        m.kind === 'video' ? (
          <video
            key={m.id}
            src={m.url}
            controls
            className="h-32 rounded border border-white/10 shrink-0"
          />
        ) : (
          <img
            key={m.id}
            src={m.url}
            alt=""
            className="h-32 rounded border border-white/10 shrink-0 object-cover"
          />
        )
      )}
    </div>
  );
}

/** Staged (not-yet-uploaded) photo/video picker, shared by the new-recipe form
 * and edit mode's "add media" — same UI FoodCapture.tsx uses for food logs. */
function MediaPicker({
  media,
  onAdd,
  onRemove,
}: {
  media: PickedMedia[];
  onAdd: (files: FileList | null) => void;
  onRemove: (url: string) => void;
}) {
  const photoRef = useRef<HTMLInputElement>(null);
  const videoRef = useRef<HTMLInputElement>(null);
  return (
    <div>
      {media.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-2">
          {media.map(m => (
            <div key={m.url} className="relative w-20 h-20">
              {m.kind === 'video' ? (
                <video
                  src={m.url}
                  className="w-20 h-20 object-cover rounded border border-white/10"
                />
              ) : (
                <img
                  src={m.url}
                  alt=""
                  className="w-20 h-20 object-cover rounded border border-white/10"
                />
              )}
              <button
                onClick={() => onRemove(m.url)}
                className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-black/70 text-white text-xs flex items-center justify-center hover:bg-black"
                title="Remove"
              >
                ×
              </button>
              {m.kind === 'video' && (
                <span className="absolute bottom-1 left-1 text-xs">🎥</span>
              )}
            </div>
          ))}
        </div>
      )}
      <input
        ref={photoRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={e => {
          onAdd(e.target.files);
          e.target.value = '';
        }}
      />
      <input
        ref={videoRef}
        type="file"
        accept="video/*"
        multiple
        className="hidden"
        onChange={e => {
          onAdd(e.target.files);
          e.target.value = '';
        }}
      />
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => photoRef.current?.click()}
          className="px-3 py-1.5 border border-white/20 text-[var(--color-text)] rounded-lg hover:bg-white/10 transition-colors text-sm"
        >
          📷 Photo
        </button>
        <button
          type="button"
          onClick={() => videoRef.current?.click()}
          className="px-3 py-1.5 border border-white/20 text-[var(--color-text)] rounded-lg hover:bg-white/10 transition-colors text-sm"
        >
          🎥 Video
        </button>
      </div>
    </div>
  );
}

/**
 * The recipe collection — the "Recipes" section of the Food tab. Recipes still
 * live behind the `/api/cookbook` endpoints (see api.cookbook.*); the rename to
 * "Food" is a UI grouping, not a backend change.
 */
export function RecipeList() {
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
  const [newMedia, setNewMedia] = useState<PickedMedia[]>([]);
  const [showImport, setShowImport] = useState(false);
  const [importText, setImportText] = useState('');
  const [importUrl, setImportUrl] = useState('');
  const [importError, setImportError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const {
    status: dictateStatus,
    error: dictateError,
    start: startDictate,
    stop: stopDictate,
  } = useRecorder(t => setNewContent(prev => (prev ? `${prev} ${t}` : t)));
  const dictating = dictateStatus === 'recording';
  const transcribing = dictateStatus === 'transcribing';

  useEffect(
    () => () => newMedia.forEach(m => URL.revokeObjectURL(m.url)),
    [newMedia]
  );

  const addNewMedia = (files: FileList | null) => {
    if (!files) return;
    const picked = Array.from(files).map(file => ({
      file,
      url: URL.createObjectURL(file),
      kind: mediaKind(file.type),
    }));
    setNewMedia(prev => [...prev, ...picked]);
  };

  const removeNewMedia = (url: string) => {
    setNewMedia(prev => {
      const gone = prev.find(m => m.url === url);
      if (gone) URL.revokeObjectURL(gone.url);
      return prev.filter(m => m.url !== url);
    });
  };

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
    mutationFn: (data: {
      title: string;
      content: string;
      tags?: string[];
      media?: File[];
    }) => api.cookbook.create(data),
    onSuccess: () => {
      invalidate();
      newMedia.forEach(m => URL.revokeObjectURL(m.url));
      setNewTitle('');
      setNewContent('');
      setNewTags('');
      setNewMedia([]);
      setShowNewRecipe(false);
    },
  });

  const updateRecipe = useMutation({
    mutationFn: ({
      id,
      ...data
    }: {
      id: string;
      title: string;
      content: string;
      tags: string[];
    }) => api.cookbook.update(id, data),
    onSuccess: () => {
      invalidate();
      setEditingId(null);
    },
  });

  const deleteRecipe = useMutation({
    mutationFn: (id: string) => api.cookbook.delete(id),
    onSuccess: invalidate,
  });

  const addMedia = useMutation({
    mutationFn: ({ id, files }: { id: string; files: File[] }) =>
      api.cookbook.addMedia(id, files),
    onSuccess: invalidate,
  });

  const deleteMedia = useMutation({
    mutationFn: (mediaId: string) => api.cookbook.deleteMedia(mediaId),
    onSuccess: invalidate,
  });

  const importRecipe = useMutation({
    mutationFn: (data: { text?: string; url?: string }) =>
      api.cookbook.importRecipe(data),
    onSuccess: result => {
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

  const { selIndex, next, prev, isSelected, scrollSelectedIntoView } =
    useListSelection(recipes?.length, 1);

  useShortcutScope(1, {
    next,
    prev,
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

  const formatDate = (date: string) =>
    new Intl.DateTimeFormat('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    }).format(new Date(date));

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      <div className="flex items-center justify-end gap-2 mb-4">
        <button
          onClick={() => {
            setShowImport(!showImport);
            setShowNewRecipe(false);
          }}
          className="px-4 py-2 border border-white/20 text-[var(--color-text)] rounded-lg hover:bg-white/10 transition-colors"
        >
          Import
        </button>
        <button
          onClick={() => {
            setShowNewRecipe(!showNewRecipe);
            setShowImport(false);
          }}
          className="px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors"
        >
          + New Recipe
        </button>
      </div>

      <div className="mb-4">
        <input
          type="text"
          value={searchQuery}
          onChange={e => {
            setSearchQuery(e.target.value);
            setSelectedTag(null);
          }}
          placeholder="Search recipes..."
          className="w-full bg-[var(--color-surface)] border border-white/10 rounded-lg px-4 py-2 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
        />
        {(recipeTags?.length ?? 0) > 0 && (
          <div className="tag-row flex flex-wrap gap-1.5 mt-2">
            {recipeTags?.map(tag => (
              <button
                key={tag.name}
                onClick={() => {
                  setSelectedTag(selectedTag === tag.name ? null : tag.name);
                  setSearchQuery('');
                }}
                className={`px-3 py-1 text-xs rounded-full border transition-colors ${
                  selectedTag === tag.name
                    ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                    : 'border-white/20 text-[var(--color-text-muted)] hover:border-white/40 hover:text-[var(--color-text)]'
                }`}
              >
                #{tag.name}
                <span className="ml-1 opacity-60">({tag.count})</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {showNewRecipe && (
        <div className="mb-4 p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
          <input
            value={newTitle}
            onChange={e => setNewTitle(e.target.value)}
            autoFocus
            onKeyDown={e => {
              if (e.key === 'Escape') setShowNewRecipe(false);
            }}
            placeholder="Recipe title..."
            className="w-full bg-transparent text-[var(--color-text)] font-medium placeholder:text-[var(--color-text-muted)] focus:outline-none border border-white/10 rounded p-2 mb-2"
          />
          <div className="relative">
            <textarea
              value={newContent}
              onChange={e => setNewContent(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Escape') setShowNewRecipe(false);
              }}
              placeholder={'## Ingredients\n- ...\n\n## Instructions\n1. ...'}
              rows={8}
              className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none border border-white/10 rounded p-2 pr-12 mb-2"
            />
            <button
              type="button"
              onClick={() => (dictating ? stopDictate() : void startDictate())}
              disabled={transcribing}
              title={dictating ? 'Stop recording' : 'Dictate'}
              className={`absolute top-2 right-2 w-9 h-9 rounded-full flex items-center justify-center transition-colors ${
                dictating
                  ? 'bg-red-500 text-white animate-pulse'
                  : 'bg-white/10 text-[var(--color-text)] hover:bg-white/20'
              } disabled:opacity-50`}
            >
              {transcribing ? '…' : '🎤'}
            </button>
          </div>
          {dictateError && (
            <div className="mb-2 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded text-sm text-red-400">
              {dictateError}
            </div>
          )}
          <input
            value={newTags}
            onChange={e => setNewTags(e.target.value)}
            placeholder="Tags, comma separated (e.g. soup, quick, chicken)"
            className="w-full bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none border border-white/10 rounded p-2 mb-2"
          />
          <MediaPicker
            media={newMedia}
            onAdd={addNewMedia}
            onRemove={removeNewMedia}
          />
          <div className="flex justify-end gap-2 mt-2">
            <button
              onClick={() => setShowNewRecipe(false)}
              className="px-3 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              Cancel
            </button>
            <button
              onClick={() =>
                createRecipe.mutate({
                  title: newTitle.trim(),
                  content: newContent.trim(),
                  tags: splitTagInput(newTags),
                  media: newMedia.map(m => m.file),
                })
              }
              disabled={
                !newTitle.trim() || !newContent.trim() || createRecipe.isPending
              }
              className="px-3 py-1 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
            >
              {createRecipe.isPending ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      )}

      {showImport && (
        <div className="mb-4 p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
          <textarea
            value={importText}
            onChange={e => {
              setImportText(e.target.value);
              if (e.target.value) setImportUrl('');
            }}
            onKeyDown={e => {
              if (e.key === 'Escape') setShowImport(false);
            }}
            placeholder="Paste a recipe from anywhere — the AI will clean it up..."
            rows={5}
            autoFocus
            className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none border border-white/10 rounded p-2 mb-2"
          />
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xs text-[var(--color-text-muted)]">or</span>
            <input
              value={importUrl}
              onChange={e => {
                setImportUrl(e.target.value);
                if (e.target.value) setImportText('');
              }}
              placeholder="https://... recipe page URL"
              className="flex-1 bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none border border-white/10 rounded p-2"
            />
          </div>
          {importError && (
            <div className="mb-2 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded text-sm text-red-400">
              {importError}
            </div>
          )}
          <div className="flex justify-end gap-2">
            <button
              onClick={() => {
                setShowImport(false);
                setImportError(null);
              }}
              className="px-3 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              Cancel
            </button>
            <button
              onClick={() => {
                setImportError(null);
                importRecipe.mutate(
                  importText.trim()
                    ? { text: importText.trim() }
                    : { url: importUrl.trim() }
                );
              }}
              disabled={
                (!importText.trim() && !importUrl.trim()) ||
                importRecipe.isPending
              }
              className="px-3 py-1 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
            >
              {importRecipe.isPending ? 'Extracting recipe…' : 'Import'}
            </button>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto space-y-4">
        {isLoading && (
          <div className="text-[var(--color-text-muted)]">Loading...</div>
        )}

        {recipes?.map((recipe, idx) => {
          const tags = parseTags(recipe.tags);
          const editing = editingId === recipe.id;
          const expanded = expandedId === recipe.id || editing;
          return (
            <div
              key={recipe.id}
              ref={scrollSelectedIntoView(idx)}
              className={`p-4 bg-[var(--color-surface)] rounded-lg border ${
                isSelected(idx)
                  ? 'border-[var(--color-primary)]'
                  : 'border-white/10'
              }`}
            >
              <div className="flex items-start justify-between gap-2 mb-1">
                <button
                  onClick={() => setExpandedId(expanded ? null : recipe.id)}
                  className="text-left text-base font-bold text-[var(--color-text)] hover:text-[var(--color-primary)] transition-colors"
                >
                  {recipe.title}
                </button>
                <div className="flex gap-2 shrink-0">
                  <button
                    onClick={() => startEdit(recipe)}
                    className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => deleteRecipe.mutate(recipe.id)}
                    className="text-sm text-red-400 hover:text-red-300"
                  >
                    Delete
                  </button>
                </div>
              </div>
              <div className="flex items-center gap-2 text-sm text-[var(--color-text-muted)] mb-2">
                <span>{formatDate(recipe.createdAt)}</span>
                {recipe.sourceUrl && (
                  <a
                    href={recipe.sourceUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="underline hover:text-[var(--color-text)] truncate"
                  >
                    source
                  </a>
                )}
              </div>

              {editing ? (
                <div>
                  <input
                    value={editTitle}
                    onChange={e => setEditTitle(e.target.value)}
                    placeholder="Recipe title..."
                    className="w-full bg-transparent text-[var(--color-text)] font-medium focus:outline-none border border-white/10 rounded p-2 mb-2"
                  />
                  <textarea
                    value={editContent}
                    onChange={e => setEditContent(e.target.value)}
                    rows={10}
                    className="w-full bg-transparent text-[var(--color-text)] resize-none focus:outline-none border border-white/10 rounded p-2 mb-2"
                  />
                  <input
                    value={editTags}
                    onChange={e => setEditTags(e.target.value)}
                    placeholder="Tags, comma separated"
                    className="w-full bg-transparent text-sm text-[var(--color-text)] focus:outline-none border border-white/10 rounded p-2 mb-2"
                  />
                  {recipe.media.length > 0 && (
                    <div className="flex flex-wrap gap-2 mb-2">
                      {recipe.media.map(m => (
                        <div key={m.id} className="relative w-20 h-20">
                          {m.kind === 'video' ? (
                            <video
                              src={m.url}
                              className="w-20 h-20 object-cover rounded border border-white/10"
                            />
                          ) : (
                            <img
                              src={m.url}
                              alt=""
                              className="w-20 h-20 object-cover rounded border border-white/10"
                            />
                          )}
                          <button
                            onClick={() => deleteMedia.mutate(m.id)}
                            disabled={deleteMedia.isPending}
                            className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-black/70 text-white text-xs flex items-center justify-center hover:bg-black disabled:opacity-50"
                            title="Remove"
                          >
                            ×
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                  <EditMediaAdder
                    onPick={files =>
                      files &&
                      addMedia.mutate({
                        id: recipe.id,
                        files: Array.from(files),
                      })
                    }
                    busy={addMedia.isPending}
                  />
                  <div className="flex justify-end gap-2 mt-2">
                    <button
                      onClick={() => setEditingId(null)}
                      className="px-3 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={() =>
                        updateRecipe.mutate({
                          id: recipe.id,
                          title: editTitle.trim(),
                          content: editContent,
                          tags: splitTagInput(editTags),
                        })
                      }
                      disabled={!editTitle.trim() || updateRecipe.isPending}
                      className="px-3 py-1 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
                    >
                      Save
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  {expanded && <MediaGallery media={recipe.media} />}
                  {expanded ? (
                    <MessageMarkdown content={recipe.content} />
                  ) : (
                    <div className="content-text text-[var(--color-text)] whitespace-pre-wrap line-clamp-3">
                      {recipe.content}
                    </div>
                  )}
                </>
              )}

              {tags.length > 0 && !editing && (
                <div className="tag-row flex flex-wrap gap-1.5 mt-2">
                  {tags.map(tag => (
                    <span
                      key={tag}
                      className="px-2 py-0.5 text-xs rounded border border-[var(--color-primary)]/40 text-[var(--color-primary)] bg-[var(--color-primary)]/10"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        })}

        {recipes?.length === 0 && !isLoading && (
          <div className="text-center text-[var(--color-text-muted)] py-12">
            {searchQuery
              ? 'No recipes found'
              : 'No recipes yet. Add one by hand, import from a page, or describe cooking one in a food log entry.'}
          </div>
        )}
      </div>
    </div>
  );
}

/** "Add photo/video" buttons for an existing recipe in edit mode — media is
 * uploaded immediately (the recipe already has an id), unlike the new-recipe
 * form which stages files until the whole thing is saved. */
function EditMediaAdder({
  onPick,
  busy,
}: {
  onPick: (files: FileList | null) => void;
  busy: boolean;
}) {
  const photoRef = useRef<HTMLInputElement>(null);
  const videoRef = useRef<HTMLInputElement>(null);
  return (
    <div className="flex gap-2 mb-2">
      <input
        ref={photoRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={e => {
          onPick(e.target.files);
          e.target.value = '';
        }}
      />
      <input
        ref={videoRef}
        type="file"
        accept="video/*"
        multiple
        className="hidden"
        onChange={e => {
          onPick(e.target.files);
          e.target.value = '';
        }}
      />
      <button
        type="button"
        onClick={() => photoRef.current?.click()}
        disabled={busy}
        className="px-3 py-1.5 border border-white/20 text-[var(--color-text)] rounded-lg hover:bg-white/10 transition-colors text-sm disabled:opacity-50"
      >
        📷 Add photo
      </button>
      <button
        type="button"
        onClick={() => videoRef.current?.click()}
        disabled={busy}
        className="px-3 py-1.5 border border-white/20 text-[var(--color-text)] rounded-lg hover:bg-white/10 transition-colors text-sm disabled:opacity-50"
      >
        🎥 Add video
      </button>
    </div>
  );
}
