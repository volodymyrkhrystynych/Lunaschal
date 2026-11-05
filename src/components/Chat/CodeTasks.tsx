import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type CodeTask } from '@/hooks/api';
import {
  canContinue,
  canDiscard,
  canMessage,
  canPush,
  eventLabel,
  isActive,
  isDetail,
  lineTotals,
  needsYou,
  parseDiff,
  pollIntervalMs,
  statusLabel,
} from '@/lib/codeTasks';

const btn =
  'px-3 py-1 text-sm rounded-lg border border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-white/20 transition-colors disabled:opacity-40 disabled:cursor-not-allowed';
const primary =
  'px-3 py-1 text-sm bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50';

/**
 * The Code page of Chat: tasks for the server's local coding agent. The iPhone
 * is where this is mostly used (apple/App/CodeViews.swift); this is the same
 * surface for the desktop, with the same words from src/lib/codeTasks.ts.
 */
export function CodeTasks() {
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ['code', 'tasks'],
    queryFn: api.code.list,
    refetchInterval: q => pollIntervalMs(q.state.data?.tasks),
  });
  const create = useMutation({
    mutationFn: (prompt: string) => api.code.create({ prompt }),
    onSuccess: task => {
      setDraft('');
      setSelected(task.id);
      queryClient.invalidateQueries({ queryKey: ['code'] });
    },
  });
  const tasks = data?.tasks ?? [];

  if (selected)
    return <CodeTaskDetail id={selected} onBack={() => setSelected(null)} />;

  const groups: [string, CodeTask[]][] = [
    ['Needs you', tasks.filter(needsYou)],
    ['Working', tasks.filter(isActive)],
    ['Finished', tasks.filter(t => !needsYou(t) && !isActive(t))],
  ];
  return (
    <div className="h-full overflow-y-auto p-4 space-y-4">
      <div className="space-y-2">
        <textarea
          value={draft}
          onChange={e => setDraft(e.target.value)}
          placeholder="Describe a change for the agent…"
          rows={3}
          className="w-full rounded-lg bg-[var(--color-surface)] border border-white/10 p-2 text-sm"
        />
        <div className="flex items-center gap-2">
          <button
            className={primary}
            disabled={!draft.trim() || create.isPending}
            onClick={() => create.mutate(draft.trim())}
          >
            Start planning
          </button>
          <span className="text-xs text-[var(--color-text-muted)]">
            It writes a plan first; nothing changes until you approve it.
          </span>
        </div>
        {create.error && (
          <p className="text-xs text-red-400">{create.error.message}</p>
        )}
      </div>
      {groups.map(([title, list]) =>
        list.length === 0 ? null : (
          <section key={title}>
            <h3 className="text-sm font-medium text-[var(--color-text)] mb-1">
              {title}
            </h3>
            <ul className="space-y-1">
              {list.map(t => {
                const lines = lineTotals(t);
                return (
                  <li key={t.id}>
                    <button
                      onClick={() => setSelected(t.id)}
                      className="w-full text-left rounded-lg px-3 py-2 hover:bg-white/5"
                    >
                      <div className="text-sm text-[var(--color-text)]">
                        {t.title}
                      </div>
                      <div className="text-xs text-[var(--color-text-muted)]">
                        {statusLabel(t.status)}
                        {lines.added + lines.removed > 0 &&
                          ` · +${lines.added} −${lines.removed}`}
                        {t.testExit !== null &&
                          (t.testExit === 0
                            ? ' · tests passed'
                            : ' · tests failed')}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        )
      )}
    </div>
  );
}

function CodeTaskDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const queryClient = useQueryClient();
  const [reply, setReply] = useState('');
  const [steps, setSteps] = useState(false);
  const [diffOpen, setDiffOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ['code', 'task', id],
    queryFn: () => api.code.get(id),
    refetchInterval: q =>
      q.state.data && isActive(q.state.data.task) ? 3000 : 20000,
  });
  const diff = useQuery({
    queryKey: ['code', 'diff', id],
    queryFn: () => api.code.diff(id),
    enabled: diffOpen,
  });
  const act = useMutation({
    mutationFn: (f: () => Promise<CodeTask>) => f(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['code'] }),
  });

  const task = data?.task;
  if (!task)
    return (
      <div className="p-4 text-sm text-[var(--color-text-muted)]">Loading…</div>
    );
  const events = (data?.events ?? []).filter(e => steps || !isDetail(e));
  const question =
    task.pending?.kind === 'question' ? task.pending.request.question : null;

  return (
    <div className="h-full overflow-y-auto p-4 space-y-4">
      <div className="flex items-center gap-2">
        <button className={btn} onClick={onBack}>
          ← Tasks
        </button>
        <span className="text-xs text-[var(--color-text-muted)]">
          {statusLabel(task.status)} · {task.harness}
        </span>
        <div className="ml-auto flex gap-2">
          {canPush(task) && (
            <button
              className={primary}
              disabled={act.isPending}
              onClick={() =>
                confirm(
                  'Push agent/' + task.id + ' and open a pull request?'
                ) && act.mutate(() => api.code.push(task.id))
              }
            >
              Push &amp; open PR
            </button>
          )}
          {canContinue(task) && (
            <button
              className={btn}
              onClick={() => act.mutate(() => api.code.resume(task.id))}
            >
              Continue
            </button>
          )}
          {isActive(task) && (
            <button
              className={btn}
              onClick={() => act.mutate(() => api.code.cancel(task.id))}
            >
              Cancel
            </button>
          )}
          {canDiscard(task) && (
            <button
              className={btn}
              onClick={() => act.mutate(() => api.code.discard(task.id))}
            >
              Discard
            </button>
          )}
        </div>
      </div>
      <h2 className="text-lg text-[var(--color-text)]">{task.title}</h2>
      {task.error && <p className="text-sm text-red-400">{task.error}</p>}
      {act.error && <p className="text-sm text-red-400">{act.error.message}</p>}
      {question && (
        <div className="rounded-lg bg-yellow-500/10 p-3 text-sm">
          <div className="font-medium">The agent asks</div>
          <div>{question}</div>
        </div>
      )}
      {task.plan && (
        <div className="rounded-lg bg-white/5 p-3 text-sm space-y-2">
          <div className="font-medium">Plan</div>
          <div className="whitespace-pre-wrap">{task.plan}</div>
          {task.status === 'awaiting_plan' && (
            <button
              className={primary}
              onClick={() => act.mutate(() => api.code.approve(task.id))}
            >
              Approve &amp; build
            </button>
          )}
        </div>
      )}
      {(task.summary || task.testExit !== null || task.diffStat) && (
        <div className="rounded-lg bg-white/5 p-3 text-sm space-y-2">
          <div className="font-medium">Result</div>
          {task.summary && (
            <div className="whitespace-pre-wrap">{task.summary}</div>
          )}
          {task.testExit !== null && (
            <details>
              <summary
                className={
                  task.testExit === 0 ? 'text-green-400' : 'text-red-400'
                }
              >
                {task.testExit === 0
                  ? 'Tests passed'
                  : `Tests failed (exit ${task.testExit})`}
              </summary>
              <pre className="text-xs overflow-x-auto">{task.testOutput}</pre>
            </details>
          )}
          {task.diffStat && (
            <details
              onToggle={e => setDiffOpen((e.target as HTMLDetailsElement).open)}
            >
              <summary>{task.diffStat.length} files changed</summary>
              {diff.error && (
                <p className="text-xs text-red-400">{diff.error.message}</p>
              )}
              {parseDiff(diff.data?.patch ?? '').map(f => (
                <details key={f.path} className="mt-1">
                  <summary className="font-mono text-xs">{f.path}</summary>
                  {f.hunks.map((h, i) => (
                    <pre key={i} className="text-[11px] overflow-x-auto">
                      <div className="text-[var(--color-text-muted)]">
                        {h.header}
                      </div>
                      {h.lines.map((l, j) => (
                        <div
                          key={j}
                          className={
                            l.kind === 'added'
                              ? 'bg-green-500/15'
                              : l.kind === 'removed'
                                ? 'bg-red-500/15'
                                : ''
                          }
                        >
                          {(l.newNumber ?? l.oldNumber ?? '')
                            .toString()
                            .padStart(4)}{' '}
                          {l.kind === 'added'
                            ? '+'
                            : l.kind === 'removed'
                              ? '−'
                              : ' '}
                          {l.text}
                        </div>
                      ))}
                    </pre>
                  ))}
                </details>
              ))}
            </details>
          )}
          {task.prUrl && (
            <a
              className="text-[var(--color-primary)] hover:underline"
              href={task.prUrl}
              target="_blank"
              rel="noreferrer"
            >
              Pull request
            </a>
          )}
        </div>
      )}
      <section>
        <div className="flex items-center justify-between mb-1">
          <h3 className="text-sm font-medium text-[var(--color-text)]">
            Activity
          </h3>
          <label className="text-xs text-[var(--color-text-muted)]">
            <input
              type="checkbox"
              checked={steps}
              onChange={e => setSteps(e.target.checked)}
            />{' '}
            model &amp; tool steps
          </label>
        </div>
        <ul className="space-y-1 text-sm">
          {events.map(e => (
            <li
              key={e.seq}
              className={
                isDetail(e)
                  ? 'text-xs text-[var(--color-text-muted)]'
                  : 'whitespace-pre-wrap'
              }
            >
              {eventLabel(e)}
            </li>
          ))}
        </ul>
      </section>
      {canMessage(task) && (
        <div className="flex gap-2">
          <input
            value={reply}
            onChange={e => setReply(e.target.value)}
            placeholder={
              task.status === 'awaiting_plan'
                ? 'What should change in the plan?'
                : question
                  ? 'Your answer'
                  : 'Ask for a follow-up change'
            }
            className="flex-1 rounded-lg bg-[var(--color-surface)] border border-white/10 px-2 py-1 text-sm"
          />
          <button
            className={primary}
            disabled={!reply.trim() || act.isPending}
            onClick={() => {
              const text = reply.trim();
              setReply('');
              act.mutate(() =>
                task.status === 'awaiting_plan'
                  ? api.code.revise(task.id, text)
                  : api.code.message(task.id, text)
              );
            }}
          >
            Send
          </button>
        </div>
      )}
    </div>
  );
}
