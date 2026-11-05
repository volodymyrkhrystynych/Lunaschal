import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type Goal } from '@/hooks/api';
import { CARD } from './card';

const statusLabel: Record<Goal['status'], string> = {
  active: 'In progress',
  earned: 'Reward earned',
  redeemed: 'Reward redeemed',
  cancelled: 'Cancelled',
  needs_review: 'Evidence needs review',
};

function GoalRow({
  goal,
  onOpenEntry,
}: {
  goal: Goal;
  onOpenEntry?: (id: string) => void;
}) {
  const client = useQueryClient();
  const action = useMutation({
    mutationFn: (kind: 'check' | 'redeem' | 'cancel') =>
      kind === 'check'
        ? api.goals.check(goal.id)
        : api.goals.update(goal.id, kind),
    onSuccess: () => client.invalidateQueries({ queryKey: ['goals'] }),
  });
  const unresolved = goal.days.filter(
    day => day.verdict === 'uncertain'
  ).length;
  return (
    <article className="border-t border-[var(--color-border)] pt-3 mt-3">
      <h3 className="font-medium">{goal.title}</h3>
      <p className="text-sm">{goal.criteria}</p>
      <p className="text-sm mt-1">
        {goal.currentStreak}/{goal.targetDays} consecutive days ·{' '}
        {statusLabel[goal.status]}
      </p>
      <p className="text-sm">Prize: {goal.reward}</p>
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
      <div className="flex gap-3 mt-2 text-sm">
        {goal.status !== 'cancelled' && (
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
        {goal.status !== 'redeemed' && goal.status !== 'cancelled' && (
          <button
            disabled={action.isPending}
            onClick={() => action.mutate('cancel')}
          >
            Cancel goal
          </button>
        )}
      </div>
      {action.error && (
        <p role="alert" className="text-sm">
          {action.error.message}
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
    </article>
  );
}

export function GoalsCard({
  onOpenEntry,
}: {
  onOpenEntry?: (id: string) => void;
}) {
  const { data, error, isLoading } = useQuery({
    queryKey: ['goals'],
    queryFn: api.goals.list,
    refetchInterval: 15000,
  });
  return (
    <section className={CARD} aria-label="Goals and rewards">
      <h2 className="text-sm font-semibold">Goals &amp; Rewards</h2>
      <p className="text-xs text-[var(--color-text-muted)]">
        Today counts as soon as it is logged. Days roll over at 4am.
      </p>
      {isLoading && <p>Loading goals…</p>}
      {error && <p role="alert">Could not load goals: {error.message}</p>}
      {data?.length === 0 && (
        <p className="mt-2 text-sm">
          Ask chat to track a daily goal and prize, such as “Pushups for seven
          days earns a theater trip.”
        </p>
      )}
      {data?.map(goal => (
        <GoalRow key={goal.id} goal={goal} onOpenEntry={onOpenEntry} />
      ))}
    </section>
  );
}
