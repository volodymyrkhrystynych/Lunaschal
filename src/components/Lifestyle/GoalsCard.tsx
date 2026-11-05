import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type Goal, type GoalPeriod } from '@/hooks/api';
import { horizonHeading, streakLine, unsetPrompt } from '@/lib/goals';
import { CARD } from './card';

const INPUT =
  'w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]';

function useInvalidateGoals() {
  const client = useQueryClient();
  return () => client.invalidateQueries({ queryKey: ['goals'] });
}

/** Every change a goal went through is a journal entry; this lists them. */
function GoalHistory({ goal }: { goal: Goal }) {
  const [open, setOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ['goals', goal.id, 'history'],
    queryFn: () => api.goals.history(goal.id),
    enabled: open,
  });
  return (
    <details
      className="mt-2 text-sm"
      onToggle={e => setOpen((e.target as HTMLDetailsElement).open)}
    >
      <summary className="cursor-pointer">History</summary>
      <ul className="space-y-2 mt-2">
        {data?.map(entry => (
          <li key={entry.id}>
            <span className="text-xs text-[var(--color-text-muted)]">
              {new Date(entry.createdAt).toLocaleString()}
            </span>
            <p className="whitespace-pre-wrap">{entry.content}</p>
          </li>
        ))}
      </ul>
    </details>
  );
}

function RuleEvidence({
  goal,
  onOpenEntry,
}: {
  goal: Goal;
  onOpenEntry?: (id: string) => void;
}) {
  const unresolved = goal.days.filter(
    day => day.verdict === 'uncertain'
  ).length;
  return (
    <>
      <p className="text-sm">{goal.criteria}</p>
      <p className="text-sm mt-1">{streakLine(goal)}</p>
      {goal.reward && <p className="text-sm">Prize: {goal.reward}</p>}
      <p className="text-xs text-[var(--color-text-muted)]">
        Starting {goal.startDate} · Best streak: {goal.bestStreak} days
        {goal.lastCheckedAt
          ? ` · Checked ${new Date(goal.lastCheckedAt).toLocaleString()}`
          : ' · Not checked yet'}
      </p>
      {unresolved > 0 && (
        <p role="status" className="text-sm">
          {unresolved} day(s) need clearer evidence or another check.
        </p>
      )}
      {goal.needsReview && (
        <p role="status" className="text-sm">
          The source records no longer verify the earned streak. Review the
          evidence.
        </p>
      )}
      {goal.days.length > 0 && (
        <details className="mt-2 text-sm">
          <summary className="cursor-pointer">Daily evidence</summary>
          <ul className="space-y-2 mt-2">
            {[...goal.days].reverse().map(day => (
              <li key={day.date}>
                <strong>{day.date}</strong>:{' '}
                {day.verdict === 'met'
                  ? 'Completed'
                  : day.verdict === 'uncertain'
                    ? 'Unresolved'
                    : 'No completion recorded'}
                {day.error && <p>{day.error}</p>}
                {day.evidence.map((source, index) => (
                  <div key={`${source.id}-${index}`} className="ml-2">
                    {source.kind === 'journal' && onOpenEntry ? (
                      <button
                        className="underline"
                        onClick={() => onOpenEntry(source.id)}
                      >
                        Open journal entry
                      </button>
                    ) : (
                      <span>Workout log</span>
                    )}
                    <blockquote className="whitespace-pre-wrap">
                      {source.text}
                    </blockquote>
                  </div>
                ))}
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

function GoalRow({
  goal,
  onOpenEntry,
}: {
  goal: Goal;
  onOpenEntry?: (id: string) => void;
}) {
  const invalidate = useInvalidateGoals();
  const action = useMutation({
    mutationFn: (kind: 'check' | 'redeem' | 'end') =>
      kind === 'check'
        ? api.goals.check(goal.id)
        : api.goals.update(goal.id, kind),
    onSuccess: invalidate,
  });
  return (
    <article className="border-t border-[var(--color-border)] pt-3 mt-3">
      <h4 className="font-medium">{goal.title}</h4>
      {goal.details && (
        <p className="text-sm whitespace-pre-wrap">{goal.details}</p>
      )}
      {goal.overdue && (
        <p className="text-xs text-[var(--color-text-muted)]">
          {goal.periodLabel} is over — the review will grade it.
        </p>
      )}
      {goal.hasRule && <RuleEvidence goal={goal} onOpenEntry={onOpenEntry} />}
      <div className="flex gap-3 mt-2 text-sm">
        {goal.hasRule && (
          <button
            disabled={action.isPending}
            onClick={() => action.mutate('check')}
          >
            {action.isPending && action.variables === 'check'
              ? 'Checking…'
              : 'Check now'}
          </button>
        )}
        {goal.status === 'earned' && !goal.needsReview && (
          <button
            disabled={action.isPending}
            onClick={() => action.mutate('redeem')}
          >
            Mark prize redeemed
          </button>
        )}
        <button
          disabled={action.isPending}
          onClick={() => action.mutate('end')}
        >
          End goal
        </button>
      </div>
      {action.error && (
        <p role="alert" className="text-sm">
          {action.error.message}
        </p>
      )}
      <GoalHistory goal={goal} />
    </article>
  );
}

function AddGoal({ period }: { period: GoalPeriod }) {
  const invalidate = useInvalidateGoals();
  const [title, setTitle] = useState('');
  const create = useMutation({
    mutationFn: () =>
      api.goals.create({ horizon: period.horizon, title: title.trim() }),
    onSuccess: () => {
      setTitle('');
      invalidate();
    },
  });
  return (
    <form
      className="flex gap-2 mt-2"
      onSubmit={e => {
        e.preventDefault();
        if (title.trim()) create.mutate();
      }}
    >
      <input
        aria-label={`New goal for ${horizonHeading(period.horizon).toLowerCase()}`}
        value={title}
        onChange={e => setTitle(e.target.value)}
        placeholder="Add a goal…"
        className={INPUT}
      />
      <button type="submit" disabled={!title.trim() || create.isPending}>
        Add
      </button>
    </form>
  );
}

function Overarching({ goal }: { goal: Goal | null }) {
  const invalidate = useInvalidateGoals();
  const [draft, setDraft] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (text: string) => api.goals.setOverarching(text),
    onSuccess: () => {
      setDraft(null);
      invalidate();
    },
  });
  const text = goal?.details ?? goal?.title ?? '';
  if (draft === null) {
    return (
      <div>
        {text ? (
          <p className="whitespace-pre-wrap">{text}</p>
        ) : (
          <p className="text-sm text-[var(--color-text-muted)]">
            No overarching goal yet.
          </p>
        )}
        <button className="text-sm mt-1" onClick={() => setDraft(text)}>
          {text ? 'Change' : 'Set overarching goal'}
        </button>
      </div>
    );
  }
  return (
    <form
      onSubmit={e => {
        e.preventDefault();
        if (draft.trim()) save.mutate(draft.trim());
      }}
    >
      <textarea
        aria-label="Overarching goal"
        value={draft}
        onChange={e => setDraft(e.target.value)}
        rows={4}
        className={`${INPUT} resize-y`}
      />
      <p className="text-xs text-[var(--color-text-muted)]">
        Saving writes a journal entry, so the old goal stays on record.
      </p>
      <div className="flex gap-3 mt-1 text-sm">
        <button type="submit" disabled={!draft.trim() || save.isPending}>
          Save
        </button>
        <button type="button" onClick={() => setDraft(null)}>
          Cancel
        </button>
      </div>
      {save.error && (
        <p role="alert" className="text-sm">
          {save.error.message}
        </p>
      )}
    </form>
  );
}

export function GoalsCard({
  onOpenEntry,
}: {
  onOpenEntry?: (id: string) => void;
}) {
  const { data, error, isLoading } = useQuery({
    queryKey: ['goals', 'current'],
    queryFn: api.goals.current,
    refetchInterval: 15000,
  });
  return (
    <section className={CARD} aria-label="Goals">
      <h2 className="text-sm font-semibold">Goals</h2>
      {isLoading && <p>Loading goals…</p>}
      {error && <p role="alert">Could not load goals: {error.message}</p>}
      {data && (
        <>
          <Overarching goal={data.overarching} />
          {data.horizons.map(period => (
            <section
              key={period.horizon}
              aria-label={horizonHeading(period.horizon)}
              className="mt-4"
            >
              <h3 className="text-sm font-semibold">
                {horizonHeading(period.horizon)}{' '}
                <span className="font-normal text-[var(--color-text-muted)]">
                  · {period.label}
                </span>
              </h3>
              {period.unset && (
                <p className="text-sm text-[var(--color-text-muted)]">
                  {unsetPrompt(period)}
                </p>
              )}
              {period.goals.map(goal => (
                <GoalRow key={goal.id} goal={goal} onOpenEntry={onOpenEntry} />
              ))}
              <AddGoal period={period} />
            </section>
          ))}
          <p className="text-xs text-[var(--color-text-muted)] mt-3">
            Ask chat for a daily streak with a prize, such as “Pushups every day
            this week earns a theater trip.” Days roll over at 4am.
          </p>
        </>
      )}
    </section>
  );
}
