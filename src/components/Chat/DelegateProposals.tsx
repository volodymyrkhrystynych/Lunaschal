import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type DelegateProposalRecord } from '../../hooks/api';
import { CategoryTagPicker } from '../Calendar/EventFormFields';
import {
  isEventCategory,
  type EventCategory,
} from '../../lib/calendarCategories';

interface Props {
  messageId: string;
  proposals: DelegateProposalRecord[];
}

// Once a card is resolved it collapses to one quiet line rather than
// disappearing, so the transcript keeps a record of what actually happened.
function resolvedLabel(p: DelegateProposalRecord): string {
  if (p.status === 'dismissed')
    return p.kind === 'todo_review'
      ? `Keeping “${str(p.data, 'title')}” — suggested less often`
      : 'Dismissed';
  switch (p.kind) {
    case 'calendar':
      return 'Saved to calendar';
    case 'calorie':
      return 'Logged calories';
    case 'food':
      return p.result?.calorieLogId
        ? 'Saved to your food log, with calories'
        : 'Saved to your food log';
    case 'recipe':
      return 'Saved to your recipes';
    case 'recipe_link':
      return 'Linked to your recipe';
    case 'todo':
      return `Added to today: ${str(p.data, 'title')}`;
    case 'todo_review':
      return todoReviewResolvedLabel(p);
    case 'habit':
      return `Added habit: ${str(p.data, 'title')}`;
    case 'habit_plan':
      return `Cue set for ${str(p.data, 'habitTitle')}: ${str(p.data, 'cue')}`;
    case 'unlock_gaming': {
      const rating = p.result?.rating ?? (p.data.rating as number | undefined);
      return `Games unlocked${rating ? ` · today ${'★'.repeat(rating)}` : ''}${p.result?.pausedInference ? ' · GPU freed' : ''}`;
    }
    case 'flashcards':
      return `Queued ${p.result?.count ?? 0} card${p.result?.count === 1 ? '' : 's'} for review in Learning`;
    case 'writing_note':
      return `Added note “${str(p.data, 'title')}”`;
    case 'writing_note_update':
      return `Updated note “${str(p.data, 'title')}” — the earlier version is in its history`;
    case 'screenshot_titles': {
      const renamed = p.result?.renamed ?? 0;
      const skipped = p.result?.skipped?.length ?? 0;
      return `Renamed ${renamed} screenshot run${renamed === 1 ? '' : 's'}${skipped ? ` — ${skipped} skipped` : ''}`;
    }
    case 'writing_chapter_update':
      return `Changed “${str(p.data, 'title')}” — the earlier version is in its history`;
    default:
      return 'Resolved';
  }
}

/** What a resolved "keep it or let it go?" card says it did. */
function todoReviewResolvedLabel(p: DelegateProposalRecord): string {
  const title = str(p.data, 'title');
  switch (p.result?.choice) {
    case 'snooze':
      return `Snoozed “${title}” for a week`;
    case 'breakdown': {
      const steps: string[] = Array.isArray(p.result?.steps)
        ? p.result.steps
        : [];
      return steps.length
        ? `Broke “${title}” down: ${steps.join('; ')}`
        : `Broke “${title}” down`;
    }
    default:
      return `Let go of “${title}”`;
  }
}

export const TODO_REVIEW_CHOICES: { value: string; label: string }[] = [
  { value: 'drop', label: 'Let it go' },
  { value: 'snooze', label: 'Snooze a week' },
  { value: 'breakdown', label: 'Break it into small steps' },
];

const HEADLINE: Record<string, string> = {
  calendar: 'Save as calendar event?',
  calorie: 'Log calories?',
  food: 'Add to your food log?',
  recipe: 'Save this recipe?',
  recipe_link: 'Link this meal to that recipe?',
  flashcards: 'Generate flashcards?',
  todo: 'Keep this for today?',
  todo_review: 'You keep skipping this',
  habit_plan: 'Make this the plan for that habit?',
  habit: 'Start a new habit?',
  unlock_gaming: 'Done with work — unlock games?',
  writing_note: 'Add to the notes?',
  writing_note_update: 'Update this note?',
  screenshot_titles: 'Rename these screenshots?',
  writing_chapter_update: 'Change this chapter?',
};

const ACCEPT_LABEL: Record<string, string> = {
  calendar: 'Save',
  calorie: 'Log',
  food: 'Log Meal',
  recipe: 'Save',
  recipe_link: 'Link',
  flashcards: 'Queue Cards',
  todo: 'Add to today',
  todo_review: 'Apply',
  habit_plan: 'Use as cue',
  habit: 'Add habit',
  unlock_gaming: 'Unlock gaming',
  writing_note: 'Add Note',
  writing_note_update: 'Update Note',
  screenshot_titles: 'Rename',
  writing_chapter_update: 'Update Chapter',
};

const DISMISS_LABEL: Record<string, string> = {
  todo_review: 'Keep it',
};

const ACCEPT_PENDING_LABEL: Record<string, string> = {
  calendar: 'Saving...',
  calorie: 'Logging...',
  food: 'Logging...',
  recipe: 'Saving...',
  recipe_link: 'Linking...',
  flashcards: 'Generating...',
  todo: 'Adding...',
  todo_review: 'Working...',
  writing_note: 'Saving...',
  writing_note_update: 'Saving...',
  screenshot_titles: 'Renaming...',
  writing_chapter_update: 'Saving...',
};

// Matches TodoForm.tsx — the card is the same kind of small entry form, and a
// second visual language for the same job in the same app reads as a bug.
const fieldClass =
  'bg-[var(--color-surface)] border border-white/20 rounded px-2 py-1 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] outline-none focus:border-[var(--color-primary)]';

function str(data: Record<string, unknown>, key: string): string {
  const v = data[key];
  return typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '';
}

interface ScreenshotRun {
  entryId: string;
  title: string;
  date?: string;
  time?: string;
  endTime?: string;
  shots?: number;
}

function screenshotRunsOf(data: Record<string, unknown>): ScreenshotRun[] {
  return Array.isArray(data.items) ? (data.items as ScreenshotRun[]) : [];
}

/** One run on a `screenshot_titles` card, as the user will recognise it:
 * when it was taken and how many shots. `ChatProposal.screenshotRunLabel` on
 * iOS, word for word. */
export function screenshotRunLabel(run: ScreenshotRun): string {
  let label = run.date ?? '';
  if (run.time) {
    label += ` ${run.time}`;
    if (run.endTime && run.endTime !== run.time) label += `–${run.endTime}`;
  }
  if (typeof run.shots === 'number')
    label += ` · ${run.shots} shot${run.shots === 1 ? '' : 's'}`;
  return label;
}

function categoriesOf(data: Record<string, unknown>): EventCategory[] {
  return Array.isArray(data.categoryTags)
    ? data.categoryTags.filter(isEventCategory)
    : [];
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex items-center gap-2">
      <span className="text-xs text-[var(--color-text-muted)] w-16 shrink-0">
        {label}
      </span>
      {children}
    </label>
  );
}

/** The editable body of one pending card, per kind.
 *
 * Editable because the card was read-only and the values on it are a model's
 * reading of a sentence: a due date one day out, or a priority it guessed,
 * could only be fixed by dismissing the card and going to the Tasks view to
 * type the whole thing again. `data` is the live edit state; accepting posts
 * it, and the server re-validates through the same handler either way.
 */
function ProposalForm({
  kind,
  data,
  set,
  reconstructed = false,
}: {
  kind: string;
  data: Record<string, unknown>;
  set: (patch: Record<string, unknown>) => void;
  reconstructed?: boolean;
}) {
  switch (kind) {
    case 'calendar': {
      const allDay = data.allDay === true;
      return (
        <div className="space-y-1.5">
          <input
            aria-label="Title"
            value={str(data, 'title')}
            onChange={e => set({ title: e.target.value })}
            className={`${fieldClass} w-full`}
          />
          <input
            aria-label="Description"
            value={str(data, 'description')}
            onChange={e => set({ description: e.target.value })}
            placeholder="More information…"
            className={`${fieldClass} w-full text-xs`}
          />
          {'location' in data && (
            <Field label="Location">
              <input
                value={str(data, 'location')}
                onChange={e => set({ location: e.target.value })}
                placeholder="Unknown"
                className={`${fieldClass} flex-1`}
              />
            </Field>
          )}
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            <Field label="Date">
              <input
                type="date"
                value={str(data, 'date')}
                onChange={e => set({ date: e.target.value })}
                className={fieldClass}
              />
            </Field>
            {/* An all-day event is explicitly the whole day, not merely
                untimed, so ticking it clears the clocks rather than leaving
                them to be silently ignored on save. */}
            {!reconstructed && (
              <label className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
                <input
                  type="checkbox"
                  checked={allDay}
                  onChange={e =>
                    set(
                      e.target.checked
                        ? { allDay: true, time: null, endTime: null }
                        : { allDay: false }
                    )
                  }
                />
                All day
              </label>
            )}
            {!allDay && (
              <>
                <Field label="From">
                  <input
                    type="time"
                    value={str(data, 'time')}
                    onChange={e => set({ time: e.target.value || null })}
                    className={fieldClass}
                  />
                </Field>
                <Field label="To">
                  <input
                    type="time"
                    value={str(data, 'endTime')}
                    onChange={e => set({ endTime: e.target.value || null })}
                    className={fieldClass}
                  />
                </Field>
              </>
            )}
          </div>
          <Field label="Tags">
            <input
              value={
                Array.isArray(data.tags)
                  ? (data.tags as string[]).join(', ')
                  : ''
              }
              onChange={e =>
                set({
                  tags: e.target.value
                    .split(',')
                    .map(t => t.trim())
                    .filter(Boolean),
                })
              }
              placeholder="comma, separated"
              className={`${fieldClass} flex-1 text-xs`}
            />
          </Field>
          {/* The six colour categories, the same picker (and so the same
              vocabulary and colours) the calendar's own forms use. Ticked by
              hand here because a suggested event has no transcribed
              description for the classifier to read, so without this the card
              could only be labelled after saving it and reopening it in the
              Calendar. */}
          <CategoryTagPicker
            value={categoriesOf(data)}
            onChange={categoryTags => set({ categoryTags })}
          />
        </div>
      );
    }

    // A habit the morning briefing suggests, with an if-then cue.
    case 'habit':
      return (
        <div className="space-y-1.5">
          <input
            aria-label="Habit"
            value={str(data, 'title')}
            onChange={e => set({ title: e.target.value })}
            className={`${fieldClass} w-full`}
          />
          <input
            aria-label="Cue"
            value={str(data, 'cue')}
            onChange={e => set({ cue: e.target.value })}
            className={`${fieldClass} w-full`}
          />
          {str(data, 'why') && (
            <p className="text-xs text-[var(--color-text-muted)]">
              {str(data, 'why')}
            </p>
          )}
        </div>
      );

    // An if-then plan for an obstacle that keeps coming back; it becomes
    // the habit's cue.
    case 'habit_plan':
      return (
        <div className="space-y-1.5">
          <p className="text-xs text-[var(--color-text-muted)]">
            {str(data, 'habitTitle')} — keeps getting in the way:{' '}
            {str(data, 'obstacle')}
          </p>
          <input
            aria-label="Cue"
            value={str(data, 'cue')}
            onChange={e => set({ cue: e.target.value })}
            className={`${fieldClass} w-full`}
          />
        </div>
      );

    // The end of the workday: today's provisional review, then games.
    case 'unlock_gaming': {
      const rating = Math.max(0, Math.min(5, Number(data.rating) || 0));
      const open = Array.isArray(data.open) ? (data.open as string[]) : [];
      return (
        <div className="space-y-1.5">
          <p className="text-sm font-medium">
            {rating
              ? `Today so far: ${'★'.repeat(rating)}${'☆'.repeat(5 - rating)}`
              : 'Nothing tracked today'}
          </p>
          {open.length > 0 && (
            <p className="text-xs text-[var(--color-text-muted)]">
              Still open: {open.join(', ')}
            </p>
          )}
          {str(data, 'workEnd') && (
            <p className="text-xs text-[var(--color-text-muted)]">
              Work was down to end at {str(data, 'workEnd')}.
            </p>
          )}
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={data.pauseInference !== false}
              onChange={e => set({ pauseInference: e.target.checked })}
            />
            Also pause GPU inference
          </label>
        </div>
      );
    }

    // Yesterday's unfinished bar item, carried over by the morning briefing.
    case 'todo':
      return (
        <div className="space-y-1.5">
          <input
            aria-label="Title"
            value={str(data, 'title')}
            onChange={e => set({ title: e.target.value })}
            className={`${fieldClass} w-full`}
          />
          {str(data, 'notes') && (
            <p className="text-xs text-[var(--color-text-muted)]">
              {str(data, 'notes')}
            </p>
          )}
          {str(data, 'reason') && (
            <p className="text-xs text-[var(--color-text-muted)]">
              {str(data, 'reason')}
            </p>
          )}
        </div>
      );

    // Skipped day after day in the bar: keep it (Dismiss), or one of these.
    case 'todo_review':
      return (
        <div className="space-y-1.5">
          <p className="text-sm text-[var(--color-text)]">
            {str(data, 'title')}
          </p>
          <p className="text-xs text-[var(--color-text-muted)]">
            Left undone {str(data, 'streak')} days running.
          </p>
          <div role="radiogroup" className="flex flex-wrap gap-x-4 gap-y-1">
            {TODO_REVIEW_CHOICES.map(c => (
              <label
                key={c.value}
                className="flex items-center gap-1.5 text-sm text-[var(--color-text)]"
              >
                <input
                  type="radio"
                  name="todo-review-choice"
                  value={c.value}
                  checked={(str(data, 'choice') || 'drop') === c.value}
                  onChange={() => set({ choice: c.value })}
                  className="accent-[var(--color-primary)]"
                />
                {c.label}
              </label>
            ))}
          </div>
        </div>
      );

    case 'calorie':
      return (
        <div className="flex flex-wrap gap-x-4 gap-y-1.5 items-center">
          <input
            aria-label="Description"
            value={str(data, 'description')}
            onChange={e => set({ description: e.target.value })}
            className={`${fieldClass} flex-1 min-w-40`}
          />
          <Field label="Calories">
            <input
              type="number"
              min={0}
              value={str(data, 'calories')}
              onChange={e =>
                set({
                  calories: e.target.value ? Number(e.target.value) : null,
                })
              }
              className={`${fieldClass} w-24`}
            />
          </Field>
        </div>
      );

    case 'food':
      return (
        <div className="space-y-1.5">
          <input
            aria-label="Dish"
            value={str(data, 'dish')}
            onChange={e => set({ dish: e.target.value })}
            className={`${fieldClass} w-full`}
          />
          <div className="flex flex-wrap gap-x-4 gap-y-1.5 items-center">
            <Field label="Place">
              <input
                value={str(data, 'place')}
                onChange={e => set({ place: e.target.value })}
                className={`${fieldClass} w-36`}
              />
            </Field>
            <Field label="Calories">
              <input
                type="number"
                min={0}
                value={str(data, 'calories')}
                onChange={e =>
                  set({
                    calories: e.target.value ? Number(e.target.value) : null,
                  })
                }
                placeholder="—"
                className={`${fieldClass} w-24`}
              />
            </Field>
            <Field label="Rating">
              <input
                type="number"
                min={1}
                max={5}
                value={str(data, 'rating')}
                onChange={e =>
                  set({
                    rating: e.target.value ? Number(e.target.value) : null,
                  })
                }
                placeholder="—"
                className={`${fieldClass} w-16`}
              />
            </Field>
          </div>
          <textarea
            aria-label="Notes"
            value={str(data, 'notes')}
            onChange={e => set({ notes: e.target.value })}
            rows={2}
            placeholder="What you said about it"
            className={`${fieldClass} w-full resize-none`}
          />
          {/* Said plainly because neither is an editable field here: both come
              off the message itself when the card is accepted, so an edit can't
              rewrite what was actually said. */}
          <div className="text-xs text-[var(--color-text-muted)]">
            Your photo and exactly what you said are saved with it.
          </div>
        </div>
      );

    case 'recipe':
      return (
        <div className="space-y-1.5">
          <input
            aria-label="Title"
            value={str(data, 'title')}
            onChange={e => set({ title: e.target.value })}
            className={`${fieldClass} w-full`}
          />
          <textarea
            aria-label="Content"
            value={str(data, 'content')}
            onChange={e => set({ content: e.target.value })}
            rows={10}
            className={`${fieldClass} w-full resize-none`}
          />
          <Field label="Tags">
            <input
              value={
                Array.isArray(data.tags)
                  ? (data.tags as string[]).join(', ')
                  : ''
              }
              onChange={e =>
                set({
                  tags: e.target.value
                    .split(',')
                    .map(t => t.trim())
                    .filter(Boolean),
                })
              }
              placeholder="comma, separated"
              className={`${fieldClass} flex-1 text-xs`}
            />
          </Field>
        </div>
      );

    case 'recipe_link':
      // Not editable: entryId/recipeId are the whole point of this card, and
      // rewriting either would point the link at a different row than what
      // was actually offered.
      return (
        <div className="text-sm text-[var(--color-text)]">
          <span className="font-medium">{str(data, 'dish')}</span>
          {' → '}
          <span className="font-medium">{str(data, 'recipeTitle')}</span>
        </div>
      );

    case 'flashcards':
      return (
        <div className="space-y-1.5">
          <input
            aria-label="Topic"
            value={str(data, 'topic')}
            onChange={e => set({ topic: e.target.value })}
            className={`${fieldClass} w-full`}
          />
          <div className="text-xs text-[var(--color-text-muted)]">
            I'll generate atomic cards and queue them for your approval in the
            Learning tab.
          </div>
        </div>
      );

    case 'writing_note':
      return (
        <div className="space-y-1.5">
          <div className="flex gap-2">
            <input
              aria-label="Title"
              value={str(data, 'title')}
              onChange={e => set({ title: e.target.value })}
              className={`${fieldClass} flex-1`}
            />
            <select
              aria-label="Type"
              value={str(data, 'docType') || 'note'}
              onChange={e => set({ docType: e.target.value })}
              className={fieldClass}
            >
              <option value="character">Character</option>
              <option value="outline">Outline</option>
              <option value="worldbuilding">World</option>
              <option value="note">Note</option>
            </select>
          </div>
          <textarea
            aria-label="Content"
            value={str(data, 'content')}
            onChange={e => set({ content: e.target.value })}
            rows={Math.min(12, str(data, 'content').split('\n').length + 2)}
            className={`${fieldClass} w-full resize-y`}
          />
        </div>
      );

    case 'screenshot_titles': {
      // One name per run, each under when it was taken. A run can be taken
      // off the card; the last one can't, since Dismiss already says "none".
      const runs = screenshotRunsOf(data);
      const setRuns = (next: ScreenshotRun[]) => set({ items: next });
      return (
        <div className="space-y-2">
          {runs.map((run, i) => {
            const label = screenshotRunLabel(run);
            return (
              <div key={run.entryId} className="space-y-0.5">
                <div className="text-xs text-[var(--color-text-muted)]">
                  {label}
                </div>
                <div className="flex gap-2">
                  <input
                    aria-label={`Name for ${label}`}
                    value={run.title ?? ''}
                    onChange={e =>
                      setRuns(
                        runs.map((r, j) =>
                          j === i ? { ...r, title: e.target.value } : r
                        )
                      )
                    }
                    className={`${fieldClass} flex-1`}
                  />
                  {runs.length > 1 && (
                    <button
                      type="button"
                      aria-label={`Leave out ${label}`}
                      onClick={() => setRuns(runs.filter((_, j) => j !== i))}
                      className="px-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                    >
                      ✕
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      );
    }

    case 'writing_note_update': {
      const replace = data.mode === 'replace';
      return (
        <div className="space-y-1.5">
          <div className="text-xs text-[var(--color-text-muted)]">
            {replace ? 'Replaces the whole of' : 'Adds to the end of'} “
            {str(data, 'title')}”
            {str(data, 'reason') && ` — ${str(data, 'reason')}`}
          </div>
          <textarea
            aria-label="Content"
            value={str(data, 'content')}
            onChange={e => set({ content: e.target.value })}
            rows={Math.min(12, str(data, 'content').split('\n').length + 2)}
            className={`${fieldClass} w-full resize-y`}
          />
          <label className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
            <input
              type="checkbox"
              checked={replace}
              onChange={e =>
                set({ mode: e.target.checked ? 'replace' : 'append' })
              }
              className="accent-[var(--color-primary)]"
            />
            Replace the note instead of adding to it
          </label>
        </div>
      );
    }

    case 'writing_chapter_update': {
      const replace = data.mode === 'replace_passage';
      return (
        <div className="space-y-1.5">
          <div className="text-xs text-[var(--color-text-muted)]">
            {replace ? 'Replaces a passage of' : 'Adds to the end of'} “
            {str(data, 'title')}”
            {str(data, 'reason') && ` — ${str(data, 'reason')}`}
          </div>
          {replace && (
            <blockquote className="text-xs text-[var(--color-text-muted)] border-l-2 border-white/20 pl-2 whitespace-pre-wrap">
              {str(data, 'find')}
            </blockquote>
          )}
          <textarea
            aria-label="Content"
            value={str(data, 'content')}
            onChange={e => set({ content: e.target.value })}
            rows={Math.min(12, str(data, 'content').split('\n').length + 2)}
            className={`${fieldClass} w-full resize-y`}
          />
        </div>
      );
    }

    default:
      return null;
  }
}

function PendingCard({
  proposal,
  busy,
  error,
  onResolve,
}: {
  proposal: DelegateProposalRecord;
  busy: boolean;
  error: string | null;
  onResolve: (
    action: 'accept' | 'dismiss',
    data: Record<string, unknown>
  ) => void;
}) {
  // Seeded once from what was staged; the row on the server is not touched
  // until accept, so an abandoned edit costs nothing.
  const [data, setData] = useState<Record<string, unknown>>(proposal.data);
  const set = (patch: Record<string, unknown>) =>
    setData(d => ({ ...d, ...patch }));

  return (
    <div className="rounded-lg border border-white/10 bg-[var(--color-surface)]/60 p-3">
      <div className="text-sm font-medium text-[var(--color-text)] mb-2">
        {proposal.reconstructionDay
          ? `Suggested event · ${proposal.reconstructionDay}`
          : (HEADLINE[proposal.kind] ?? 'Save this?')}
      </div>
      {proposal.evidence && (
        <p className="text-xs text-[var(--color-text-muted)] mb-2">
          {proposal.evidence}
        </p>
      )}
      {!!proposal.sources?.length && (
        <details className="text-xs text-[var(--color-text-muted)] mb-2">
          <summary>Supporting entries</summary>
          <ul>
            {proposal.sources.map(source => (
              <li key={source.id}>
                {source.label} · {source.recordedAt}
              </li>
            ))}
          </ul>
        </details>
      )}
      <ProposalForm
        kind={proposal.kind}
        data={data}
        set={set}
        reconstructed={!!proposal.reconstructionDay}
      />
      <div className="flex gap-2 justify-end mt-2">
        <button
          onClick={() => onResolve('dismiss', data)}
          disabled={busy}
          className="px-3 py-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
        >
          {DISMISS_LABEL[proposal.kind] ?? 'Dismiss'}
        </button>
        <button
          onClick={() => onResolve('accept', data)}
          disabled={busy}
          className="px-3 py-1 text-sm bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
        >
          {busy
            ? ACCEPT_PENDING_LABEL[proposal.kind]
            : proposal.reconstructionDay
              ? 'Approve event'
              : ACCEPT_LABEL[proposal.kind]}
        </button>
      </div>
      {error && <div className="mt-2 text-xs text-red-400">{error}</div>}
    </div>
  );
}

/** Delegate confirm cards for one assistant message, read straight from its
 * persisted metadata (backend/delegate/runs.py stamps each with an id and
 * 'pending' status when the run finishes) — durable across a reload or a
 * dropped connection, and resolved in place by POST
 * /api/chat/proposals/<messageId>/<id> so a card leaves "pending" only when
 * the user actually accepts or dismisses it. */
export function DelegateProposals({ messageId, proposals }: Props) {
  const queryClient = useQueryClient();

  const resolve = useMutation({
    mutationFn: ({
      proposalId,
      action,
      data,
    }: {
      proposalId: string;
      action: 'accept' | 'dismiss';
      data?: Record<string, unknown>;
    }) => api.chat.resolveProposal(messageId, proposalId, action, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['chat', 'today'] });
      queryClient.invalidateQueries({ queryKey: ['calendar'] });
      queryClient.invalidateQueries({ queryKey: ['journal'] });
      queryClient.invalidateQueries({ queryKey: ['lifestyle', 'calories'] });
      queryClient.invalidateQueries({ queryKey: ['food'] });
      queryClient.invalidateQueries({ queryKey: ['cookbook'] });
      queryClient.invalidateQueries({ queryKey: ['todos'] });
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      queryClient.invalidateQueries({ queryKey: ['chatTodos'] });
      queryClient.invalidateQueries({ queryKey: ['learning'] });
      // A Writing discussion's cards: the transcript it renders from, and the
      // project's notes the accept just wrote into.
      queryClient.invalidateQueries({ queryKey: ['chat', 'conversation'] });
      queryClient.invalidateQueries({ queryKey: ['writing'] });
    },
  });

  const busyId =
    resolve.isPending && resolve.variables
      ? resolve.variables.proposalId
      : null;

  return (
    <div className="mt-2 space-y-2">
      {proposals.some(p => p.reconstructionDay) && (
        <p className="text-sm font-medium">Suggested events from yesterday</p>
      )}
      {proposals.some(p => p.carriedFrom) && (
        <p className="text-sm font-medium">
          Carried over from yesterday — keep or dismiss
        </p>
      )}
      {proposals.map(p => {
        if (p.status !== 'pending') {
          return (
            <div
              key={p.id}
              className="flex items-baseline justify-between gap-2 rounded border border-white/5 px-2 py-1.5 opacity-60"
            >
              <span className="text-sm text-[var(--color-text)]">
                {resolvedLabel(p)}
              </span>
            </div>
          );
        }

        const failed =
          resolve.isError && resolve.variables?.proposalId === p.id
            ? resolve.error instanceof Error
              ? resolve.error.message
              : 'Could not save that — try again.'
            : null;

        return (
          <PendingCard
            key={p.id}
            proposal={p}
            busy={busyId === p.id}
            error={failed}
            onResolve={(action, data) =>
              resolve.mutate(
                action === 'accept'
                  ? { proposalId: p.id, action, data }
                  : { proposalId: p.id, action }
              )
            }
          />
        );
      })}
    </div>
  );
}
