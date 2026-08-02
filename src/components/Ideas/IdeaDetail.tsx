import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type IdeaStatus } from '../../hooks/api';
import { useShortcutScope } from '../../shortcuts/ShortcutProvider';
import { IDEA_STATUSES, statusClasses } from '../../lib/ideas';
import { SketchStrip } from './SketchStrip';

const SAVE_DEBOUNCE_MS = 1500;

interface IdeaDetailProps {
  ideaId: string;
}

export function IdeaDetail({ ideaId }: IdeaDetailProps) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [saveStatus, setSaveStatus] = useState<'saved' | 'saving' | 'unsaved'>(
    'saved'
  );
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  // Guards the first hydration so a background refetch can't clobber typing.
  const loadedRef = useRef<string>('');

  const { data: idea, isLoading } = useQuery({
    queryKey: ['ideas', ideaId],
    queryFn: () => api.ideas.get(ideaId),
  });

  useEffect(() => {
    if (idea && loadedRef.current !== idea.id) {
      loadedRef.current = idea.id;
      setTitle(idea.title);
      // Show the polished text once it exists, otherwise what was captured.
      setBody(idea.content || idea.rawContent);
      setSaveStatus('saved');
    }
  }, [idea]);

  const save = useMutation({
    mutationFn: (data: {
      title?: string;
      content?: string;
      status?: IdeaStatus;
    }) => api.ideas.update(ideaId, data),
    onSuccess: () => {
      setSaveStatus('saved');
      queryClient.invalidateQueries({ queryKey: ['ideas'] });
    },
    onError: () => setSaveStatus('unsaved'),
  });

  // Debounced autosave, matching the chapter/note editors.
  useEffect(() => {
    if (!idea || loadedRef.current !== idea.id) return;
    if (title === idea.title && body === (idea.content || idea.rawContent))
      return;
    setSaveStatus('saving');
    const timer = setTimeout(() => {
      save.mutate({ title, content: body });
    }, SAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title, body, idea?.id]);

  useShortcutScope(2, {
    drillIn: () => {
      bodyRef.current?.focus();
      return true;
    },
  });

  const remove = useMutation({
    mutationFn: () => api.ideas.remove(ideaId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['ideas'] }),
  });

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
        Loading…
      </div>
    );
  }
  if (!idea) {
    return (
      <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
        This idea no longer exists.
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-y-auto">
      <div className="flex items-center gap-2 px-4 pt-4">
        <input
          value={title}
          onChange={e => setTitle(e.target.value)}
          placeholder="Untitled idea"
          className="flex-1 bg-transparent text-lg font-medium text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none"
        />
        {/* Fixed-width slot: a status that renders nothing when idle would
            reflow the header on every autosave (the Paper toolbar lesson). */}
        <span className="w-16 shrink-0 text-right text-xs text-[var(--color-text-muted)]">
          {saveStatus === 'saving'
            ? 'Saving…'
            : saveStatus === 'unsaved'
              ? 'Unsaved'
              : 'Saved'}
        </span>
      </div>

      <div className="flex items-center gap-2 px-4 py-2">
        <select
          value={idea.status}
          onChange={e => save.mutate({ status: e.target.value as IdeaStatus })}
          aria-label="Status"
          className={`rounded px-1.5 py-0.5 text-xs ${statusClasses(idea.status)} bg-[var(--color-surface)] focus:outline-none`}
        >
          {IDEA_STATUSES.map(s => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => {
            if (
              confirm('Delete this idea? Its sketches and notes go with it.')
            ) {
              remove.mutate();
            }
          }}
          className="px-2 py-0.5 rounded text-xs text-[var(--color-text-muted)] hover:bg-white/10"
        >
          Delete
        </button>
      </div>

      <textarea
        ref={bodyRef}
        data-idea-editor
        value={body}
        onChange={e => setBody(e.target.value)}
        placeholder="What's the idea?"
        className="mx-4 min-h-48 flex-1 resize-none rounded bg-[var(--color-bg)] border border-white/10 p-3 text-sm leading-relaxed text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
      />

      {/* Once the polish step exists, `content` diverges from what was spoken;
          keeping the transcript visible is the journal's raw_content contract. */}
      {idea.content && idea.rawContent && idea.content !== idea.rawContent && (
        <details className="mx-4 mt-3 text-xs text-[var(--color-text-muted)]">
          <summary className="cursor-pointer">As captured</summary>
          <p className="mt-1 whitespace-pre-wrap">{idea.rawContent}</p>
        </details>
      )}

      <SketchStrip ideaId={ideaId} />
    </div>
  );
}
