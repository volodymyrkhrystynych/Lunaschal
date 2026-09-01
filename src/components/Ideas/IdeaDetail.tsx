import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  api,
  type Idea,
  type IdeaStatus,
  type IdeaSummary,
} from '../../hooks/api';
import { useShortcutScope } from '../../shortcuts/ShortcutProvider';
import { IDEA_STATUSES, statusClasses } from '../../lib/ideas';
import {
  changedFields,
  isDirty,
  mergeServerIdea,
  saveClasses,
  saveLabel,
  saveState,
  serverDraft,
  type IdeaDraft,
} from '../../lib/ideaSave';
import { SketchStrip } from './SketchStrip';
import { IdeaAssessment } from './IdeaAssessment';
import { IdeaDecisions } from './IdeaDecisions';
import { IdeaDiscussion } from './IdeaDiscussion';
import { IdeaPlan } from './IdeaPlan';
import { IdeaRecording } from './IdeaRecording';

const SAVE_DEBOUNCE_MS = 1500;

/**
 * How often to re-ask for an idea that has no title yet. Capture kicks off a
 * background pass that names it (backend/ai/idea_title.py), and on a local
 * model that is tens of seconds — long enough that the pane would otherwise
 * show "Untitled idea" until something else happened to refetch.
 */
const TITLE_POLL_MS = 4000;
/**
 * ...but only while the idea is new enough for that pass to still be running.
 * With no AI configured the title never arrives, and a poll with no end is a
 * request every four seconds forever.
 */
const TITLE_POLL_WINDOW_MS = 3 * 60 * 1000;
/**
 * The same poll, for the longer wait a dictated idea has: speech-to-text runs
 * before the polish and the title, and a long clip on a CPU backend takes
 * minutes. Bounded for the same reason as the title window — a transcription
 * that died with the process is reset to idle at startup, but one wedged in a
 * live process would otherwise be polled forever.
 */
const RECORDING_POLL_WINDOW_MS = 30 * 60 * 1000;

const TABS = [
  { id: 'idea', label: 'Idea' },
  { id: 'decisions', label: 'Decisions' },
  { id: 'discuss', label: 'Discuss' },
  { id: 'plan', label: 'Plan' },
] as const;

type TabId = (typeof TABS)[number]['id'];

interface IdeaDetailProps {
  ideaId: string;
  /** Switch to the Journal, at the entry a dictated idea's recording became. */
  onOpenEntry?: (entryId: string) => void;
}

const EMPTY: IdeaDraft = { title: '', body: '' };

export function IdeaDetail({ ideaId, onOpenEntry }: IdeaDetailProps) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<TabId>('idea');
  const [draft, setDraft] = useState<IdeaDraft>(EMPTY);
  const [failed, setFailed] = useState(false);
  const bodyRef = useRef<HTMLTextAreaElement>(null);

  // The last text known to be on the server. Kept apart from the query cache
  // on purpose — see the note at the top of src/lib/ideaSave.ts.
  const baseline = useRef<IdeaDraft>(EMPTY);
  const hydrated = useRef<string>('');
  // What is on screen right now, readable from effects and timers that fire
  // after the render that scheduled them.
  const draftRef = useRef<IdeaDraft>(draft);
  draftRef.current = draft;

  const { data: idea, isLoading } = useQuery({
    queryKey: ['ideas', ideaId],
    queryFn: () => api.ideas.get(ideaId),
    refetchInterval: query => {
      const row = query.state.data as Idea | undefined;
      if (!row) return false;
      const age = Date.now() - new Date(row.createdAt).getTime();
      if (age < 0) return false;
      // The idea's text is still being transcribed out of its recording: a
      // definite wait with a visible end, so it gets its own longer window.
      if (row.recording?.transcriptStatus === 'running')
        return age < RECORDING_POLL_WINDOW_MS ? TITLE_POLL_MS : false;
      if (row.title.trim()) return false;
      return age < TITLE_POLL_WINDOW_MS ? TITLE_POLL_MS : false;
    },
  });

  const save = useMutation({
    mutationFn: (data: { title?: string; content?: string }) =>
      api.ideas.update(ideaId, data).then(() => data),
    onSuccess: sent => {
      // Advance only what was actually accepted: anything typed while the
      // request was in flight is still unsaved, and must stay that way.
      baseline.current = {
        title: sent.title ?? baseline.current.title,
        body: sent.content ?? baseline.current.body,
      };
      setFailed(false);
      queryClient.invalidateQueries({ queryKey: ['ideas'] });
    },
    onError: () => setFailed(true),
  });

  // Hydrate on first load, then fold later fetches in field by field: an
  // untouched field adopts the server's value (this is how the generated title
  // lands in the box), an edited one is left alone.
  useEffect(() => {
    if (!idea) return;
    if (hydrated.current !== idea.id) {
      hydrated.current = idea.id;
      baseline.current = serverDraft(idea);
      setDraft(baseline.current);
      setFailed(false);
      return;
    }
    const merged = mergeServerIdea(draftRef.current, baseline.current, idea);
    baseline.current = merged.baseline;
    if (isDirty(merged.draft, draftRef.current)) setDraft(merged.draft);
  }, [idea]);

  const dirty = isDirty(draft, baseline.current);
  const state = saveState({ dirty, inFlight: save.isPending, failed });

  // `mutate` is stable across renders; capturing it keeps `flush` stable too,
  // which is what makes the unmount effect below fire only on unmount.
  const mutateRef = useRef(save.mutate);
  mutateRef.current = save.mutate;

  const flush = useCallback(() => {
    const fields = changedFields(draftRef.current, baseline.current);
    if (Object.keys(fields).length === 0) return;
    mutateRef.current(fields);
  }, []);

  // Save on change, and only on change: with nothing edited there is no timer,
  // so an idle pane makes no requests at all.
  useEffect(() => {
    if (!dirty || save.isPending) return;
    const timer = setTimeout(flush, SAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [draft, dirty, save.isPending, flush]);

  // Switching ideas unmounts this pane (it is keyed by id), which used to drop
  // whatever was still inside the debounce window.
  useEffect(() => () => flush(), [flush]);

  useShortcutScope(2, {
    drillIn: () => {
      setTab('idea');
      bodyRef.current?.focus();
      return true;
    },
  });

  const remove = useMutation({
    mutationFn: () => api.ideas.remove(ideaId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['ideas'] }),
  });

  const setStatus = useMutation({
    mutationFn: (status: IdeaStatus) => api.ideas.update(ideaId, { status }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ideas'] });
      queryClient.invalidateQueries({ queryKey: ['ideas', ideaId] });
    },
  });

  const { data: questions } = useQuery({
    queryKey: ['ideas', ideaId, 'questions'],
    queryFn: () => api.ideas.listQuestions(ideaId),
  });
  const openDecisions = (questions ?? []).filter(
    q => q.status === 'open'
  ).length;

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
        Loading…
      </div>
    );
  }
  if (!idea) {
    // A recorded idea is opened the moment the recording stops, which is before
    // the upload that creates it server-side has landed (and, offline, before
    // it has even been attempted). The list still holds the optimistic row, so
    // that — not a 404 — is what tells the two cases apart.
    const queued = queryClient
      .getQueryData<IdeaSummary[]>(['ideas'])
      ?.some(i => i.id === ideaId);
    return (
      <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
        {queued
          ? 'Saving this idea — the recording is on this device until it lands.'
          : 'This idea no longer exists.'}
      </div>
    );
  }

  return (
    <div
      className="flex-1 flex flex-col overflow-hidden"
      onKeyDown={e => {
        // Ctrl/Cmd+S, because "when is it actually saved" deserves an answer
        // you can force rather than wait for.
        if (e.key === 's' && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          flush();
        }
      }}
    >
      <div className="flex items-center gap-2 px-4 pt-4 shrink-0">
        <input
          value={draft.title}
          onChange={e => setDraft(d => ({ ...d, title: e.target.value }))}
          onBlur={flush}
          placeholder="Untitled idea"
          aria-label="Idea title"
          className="flex-1 bg-transparent text-lg font-medium text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none"
        />
        {/* Fixed-width slot: a status that renders nothing when idle would
            reflow the header on every save (the Paper toolbar lesson). It is a
            button because two of the four states are things you can act on —
            an unsaved draft can be pushed now, a failed one retried. */}
        <button
          type="button"
          onClick={flush}
          disabled={!dirty || save.isPending}
          title={
            state === 'saved'
              ? 'Everything here is on the server'
              : state === 'saving'
                ? 'Sending to the server'
                : state === 'error'
                  ? 'The last save failed — click to try again'
                  : 'Only in this browser — click to save now'
          }
          aria-label={`Save state: ${saveLabel(state)}`}
          className={`w-24 shrink-0 text-right text-xs disabled:cursor-default ${saveClasses(state)}`}
        >
          {state === 'dirty' || state === 'error' ? '● ' : ''}
          {saveLabel(state)}
        </button>
      </div>

      <div className="flex items-center gap-2 px-4 py-2 shrink-0">
        <select
          value={idea.status}
          onChange={e => setStatus.mutate(e.target.value as IdeaStatus)}
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

      {/* Tabs rather than one long scroll: the discussion is a chat, and a chat
          needs the whole pane — a composer buried under the plan is not one. */}
      <div
        role="tablist"
        aria-label="Idea sections"
        className="flex gap-1 px-3 border-b border-white/10 shrink-0"
      >
        {TABS.map(t => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-1.5 text-sm border-b-2 -mb-px ${
              tab === t.id
                ? 'border-[var(--color-primary)] text-[var(--color-primary)]'
                : 'border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
            }`}
          >
            {t.label}
            {t.id === 'decisions' && openDecisions > 0 && (
              <span className="ml-1.5 px-1.5 py-0.5 rounded text-xs bg-amber-500/20 text-amber-300">
                {openDecisions}
              </span>
            )}
          </button>
        ))}
      </div>

      {tab === 'idea' && (
        <div className="flex-1 flex flex-col overflow-y-auto">
          {/* Above the body, not below it: while the transcript is still coming
              this is the only thing on the screen that explains the empty
              box. */}
          {idea.recording && (
            <IdeaRecording
              recording={idea.recording}
              onOpenEntry={onOpenEntry}
            />
          )}
          <textarea
            ref={bodyRef}
            data-idea-editor
            value={draft.body}
            onChange={e => setDraft(d => ({ ...d, body: e.target.value }))}
            onBlur={flush}
            placeholder="What's the idea?"
            aria-label="Idea body"
            className="mx-4 mt-4 min-h-48 flex-1 resize-none rounded bg-[var(--color-bg)] border border-white/10 p-3 text-sm leading-relaxed text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
          />

          {/* Once the polish step exists, `content` diverges from what was
              spoken; keeping the transcript visible is the journal's
              raw_content contract. */}
          {idea.content &&
            idea.rawContent &&
            idea.content !== idea.rawContent && (
              <details className="mx-4 mt-3 text-xs text-[var(--color-text-muted)]">
                <summary className="cursor-pointer">As captured</summary>
                <p className="mt-1 whitespace-pre-wrap">{idea.rawContent}</p>
              </details>
            )}

          <SketchStrip ideaId={ideaId} />
        </div>
      )}

      {tab === 'decisions' && (
        <div className="flex-1 overflow-y-auto">
          <IdeaAssessment ideaId={ideaId} userVerdict={idea.userVerdict} />
          <IdeaDecisions ideaId={ideaId} />
        </div>
      )}

      {/* Mounted only on its own tab, so the chat owns the full height and its
          scroll position is its own. */}
      {tab === 'discuss' && <IdeaDiscussion ideaId={ideaId} />}

      {tab === 'plan' && (
        <div className="flex-1 overflow-y-auto">
          <IdeaPlan ideaId={ideaId} />
        </div>
      )}
    </div>
  );
}
