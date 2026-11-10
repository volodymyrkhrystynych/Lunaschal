import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type CodeTask, type CodeTaskEvent } from '@/hooks/api';
import { useRecorder } from '@/hooks/useRecorder';
import { RecordingButton } from '../RecordingButton';
import {
  activity,
  appendDictation,
  appendEvents,
  EVENT_PAGE,
  canContinue,
  canDiscard,
  canMessage,
  canPush,
  elapsedLabel,
  isActive,
  lineTotals,
  needsYou,
  parseDiff,
  pollIntervalMs,
  statusLabel,
  terminalLines,
  testVerdict,
  type CodeActivity,
  type TerminalLine,
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
          <DictateButton onWords={w => setDraft(d => appendDictation(d, w))} />
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
                const verdict = testVerdict(t);
                return (
                  <li key={t.id}>
                    <button
                      onClick={() => setSelected(t.id)}
                      className="w-full text-left rounded-lg px-3 py-2 hover:bg-white/5"
                    >
                      <div className="text-sm text-[var(--color-text)]">
                        {t.title}
                      </div>
                      <div className="text-xs text-[var(--color-text-muted)] flex items-center gap-1.5">
                        {isActive(t) && <PulseDot />}
                        <span>
                          {statusLabel(t.status)}
                          {lines.added + lines.removed > 0 &&
                            ` · +${lines.added} −${lines.removed}`}
                          {verdict &&
                            (verdict === 'passed'
                              ? ' · tests passed'
                              : ' · tests failed')}
                        </span>
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

/**
 * A mic that types: on Stop the server's words are added to the field to be
 * read over before sending (Chat's mic sends on Stop; a coding instruction
 * gets checked first).
 */
function DictateButton({ onWords }: { onWords: (words: string) => void }) {
  const recorder = useRecorder(text => onWords(text));
  return (
    <span className="inline-flex items-center gap-2">
      <RecordingButton
        status={recorder.status}
        starting={recorder.starting}
        action="dictate"
        title="Dictate — the words are added here to check before sending"
        testId="code-transcribe"
        onClick={() =>
          recorder.status === 'recording'
            ? recorder.stop()
            : void recorder.start()
        }
      />
      {recorder.error && (
        <span className="text-xs text-red-400">{recorder.error}</span>
      )}
    </span>
  );
}

const lineClass: Record<TerminalLine['style'], string> = {
  prompt: 'text-sky-300 font-semibold mt-2',
  text: 'text-neutral-100',
  tool: 'text-green-400',
  output: 'text-neutral-400 text-[11px]',
  thought: 'text-neutral-500 italic',
  dim: 'text-neutral-500',
  success: 'text-green-400',
  failure: 'text-red-400',
};

function TerminalRow({ line }: { line: TerminalLine }) {
  if (line.style === 'thought')
    return (
      <details className={lineClass.thought}>
        <summary className="cursor-pointer list-none truncate">
          ∴ {line.text.split('\n')[0]}
        </summary>
        <div className="whitespace-pre-wrap">{line.text}</div>
      </details>
    );
  return (
    <div className={`whitespace-pre-wrap break-words ${lineClass[line.style]}`}>
      {line.text}
    </div>
  );
}

/**
 * One task, read as a terminal session: the request, every tool the agent
 * ran, its thinking folded, the model calls and the test run, newest at the
 * bottom with the reply line under it. Lines from src/lib/codeTasks.ts's
 * `terminalLines`, shared with the phone.
 */
function CodeTaskDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const queryClient = useQueryClient();
  const [reply, setReply] = useState('');
  const [diffOpen, setDiffOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ['code', 'task', id],
    queryFn: async () => {
      // Fetch only what is new since the last poll, in pages, so a log
      // longer than one page is still read to its end.
      const held =
        queryClient.getQueryData<{ task: CodeTask; events: CodeTaskEvent[] }>([
          'code',
          'task',
          id,
        ])?.events ?? [];
      let events = held;
      for (;;) {
        const after = events.length ? events[events.length - 1].seq : 0;
        const page = await api.code.get(id, after);
        events = appendEvents(events, page.events);
        if (page.events.length < EVENT_PAGE) return { task: page.task, events };
      }
    },
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
  const lines = terminalLines(data?.events ?? []);
  const now = useTicker(!!data?.task && isActive(data.task));
  const scroller = useRef<HTMLDivElement>(null);
  // A terminal follows its newest line, unless you've scrolled up to read.
  const pinned = useRef(true);
  useEffect(() => {
    const el = scroller.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  const task = data?.task;
  if (!task)
    return (
      <div className="p-4 text-sm text-[var(--color-text-muted)]">Loading…</div>
    );
  const question =
    task.pending?.kind === 'question' ? task.pending.request.question : null;
  const working = activity(task, data?.events ?? []);
  const verdict = testVerdict(task);

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center gap-2 p-3 border-b border-white/10">
        <button className={btn} onClick={onBack}>
          ← Tasks
        </button>
        <span className="text-sm text-[var(--color-text)] truncate">
          {task.title}
        </span>
        <div className="ml-auto flex gap-2 shrink-0">
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
      <div
        ref={scroller}
        onScroll={e => {
          const el = e.currentTarget;
          pinned.current =
            el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
        className="flex-1 overflow-y-auto bg-[#121316] p-3 font-mono text-xs leading-relaxed space-y-1"
        data-testid="code-terminal"
      >
        <div className="text-neutral-500">
          [{statusLabel(task.status)}] {task.harness} · agent/{task.id}
        </div>
        {lines.map(l => (
          <TerminalRow key={l.id} line={l} />
        ))}
        {isActive(task) && (
          <div className="text-neutral-500">
            <span className="animate-pulse text-neutral-100">▌</span>{' '}
            {statusLabel(task.status).toLowerCase()}…
          </div>
        )}
      </div>
      <div className="border-t border-white/10 p-3 space-y-2">
        {working && <ActivityBanner activity={working} now={now} />}
        {act.error && (
          <p className="text-xs text-red-400">{act.error.message}</p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {task.status === 'awaiting_plan' && (
            <button
              className={primary}
              disabled={act.isPending}
              onClick={() => act.mutate(() => api.code.approve(task.id))}
            >
              Approve &amp; build
            </button>
          )}
          {verdict && (
            <span
              className={`text-xs ${verdict === 'passed' ? 'text-green-400' : 'text-red-400'}`}
            >
              {verdict === 'passed'
                ? '✓ Tests passed'
                : `✗ Tests failed (exit ${task.testExit})`}
            </span>
          )}
          {task.diffStat && task.diffStat.length > 0 && (
            <button className={btn} onClick={() => setDiffOpen(v => !v)}>
              {task.diffStat.length} files +{lineTotals(task).added} −
              {lineTotals(task).removed}
            </button>
          )}
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
          {task.prUrl && (
            <a
              className="text-sm text-[var(--color-primary)] hover:underline"
              href={task.prUrl}
              target="_blank"
              rel="noreferrer"
            >
              Pull request
            </a>
          )}
        </div>
        {diffOpen && (
          <div className="max-h-80 overflow-y-auto">
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
          </div>
        )}
        {canMessage(task) && (
          <div className="flex items-center gap-2 font-mono">
            <span className="text-sky-300">❯</span>
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
            <DictateButton
              onWords={w => setReply(r => appendDictation(r, w))}
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
    </div>
  );
}

/** A clock that ticks once a second while `on`, for the elapsed time. */
function useTicker(on: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [on]);
  return now;
}

function PulseDot() {
  return (
    <span className="relative inline-flex h-2 w-2 shrink-0">
      <span className="absolute inline-flex h-full w-full rounded-full bg-sky-400 opacity-75 animate-ping" />
      <span className="relative inline-flex h-2 w-2 rounded-full bg-sky-400" />
    </span>
  );
}

/**
 * Says, where the reply box would be, that the agent is at work: what it is
 * doing, for how long. Without it a running follow-up looked exactly like a
 * finished task with the previous run's verdict still showing.
 */
function ActivityBanner({
  activity,
  now,
}: {
  activity: CodeActivity;
  now: number;
}) {
  return (
    <div
      className="flex items-center gap-2 rounded-lg bg-sky-500/10 border border-sky-400/30 px-3 py-2"
      role="status"
      data-testid="code-activity"
    >
      <PulseDot />
      <div className="min-w-0">
        <div className="text-sm text-sky-200">
          {activity.headline}
          {activity.since !== null && (
            <span className="text-sky-300/70 tabular-nums">
              {' · '}
              {elapsedLabel(now - activity.since)}
            </span>
          )}
        </div>
        {activity.doing && (
          <div className="text-xs text-neutral-400 font-mono truncate">
            {activity.doing}
          </div>
        )}
      </div>
    </div>
  );
}
