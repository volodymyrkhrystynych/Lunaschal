import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import type { BackupStatus } from '../../hooks/api';
import { formatBytes } from '../../lib/journalAttachments';
import { FolderPicker } from './FolderPicker';
import {
  TONE_CLASSES,
  TONE_DOT,
  describeLastRun,
  describeSnapshots,
  headline,
  usedFraction,
} from '../../lib/backup';

/**
 * Health of the nightly DB + media backup (ops/backup.sh, run by
 * lunaschal-backup.timer).
 *
 * This panel is the source of truth for the destination and the retention
 * window: both live in the `settings` table, and `ops/backup.sh` asks the
 * database for them (backend/ops/backup_config.py). `ops/backup.env` keeps only
 * the tablet destination and acts as a fallback for a database that has never
 * had a path set — so the path shown here is always the path the job will use.
 * The two being separate is what let a stale `BACKUP_HDD_PATH` hide nineteen
 * days of failed backups.
 */
export function BackupSection() {
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ['backup', 'status'],
    queryFn: api.backup.status,
    // Polls only while a manual run is in flight; otherwise this is a
    // filesystem stat of a spun-down USB drive and does not need watching.
    // Annotated rather than inferred: referring to the query's own data type
    // inside its options is circular.
    refetchInterval: (q): number | false =>
      (q.state.data as BackupStatus | undefined)?.run.running ? 1500 : false,
  });

  const runNow = useMutation({
    mutationFn: api.backup.run,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['backup', 'status'] }),
  });

  const saveConfig = useMutation({
    mutationFn: api.backup.setConfig,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['backup'] }),
  });

  const [picking, setPicking] = useState(false);

  if (isLoading) {
    return <p className="text-sm text-[var(--color-text-muted)]">Checking…</p>;
  }
  if (!data) {
    return (
      <p className="text-sm text-red-400">Could not read backup status.</p>
    );
  }

  const verdict = headline(data);
  const used = usedFraction(data);
  const lastRun = describeLastRun(data);
  const running = data.run.running;

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3">
        <span
          className={`w-2 h-2 rounded-full flex-shrink-0 mt-1.5 ${TONE_DOT[verdict.tone]}`}
        />
        <div className="min-w-0">
          <span className={`text-sm font-medium ${TONE_CLASSES[verdict.tone]}`}>
            {verdict.label}
          </span>
          <p className="text-sm text-[var(--color-text)] mt-0.5">
            {verdict.detail}
          </p>
          {lastRun && (
            <p className="text-xs text-[var(--color-text-muted)] mt-1">
              {lastRun}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={() => runNow.mutate()}
          disabled={running || runNow.isPending}
          className="ml-auto shrink-0 px-3 py-1.5 rounded text-sm bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40 hover:bg-[var(--color-primary)]/30 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {running ? 'Running…' : 'Back up now'}
        </button>
      </div>

      {data.problems.length > 0 && (
        <div className="rounded border border-red-400/30 bg-red-400/5 p-3 space-y-2">
          {data.problems.map(problem => (
            <p key={problem} className="text-xs text-[var(--color-text)]">
              {problem}
            </p>
          ))}
        </div>
      )}

      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
        <Row label="Destination">
          <div className="flex items-start gap-2">
            <code className="text-[var(--color-text)] break-all">
              {data.destination ?? 'not set'}
            </code>
            <button
              type="button"
              onClick={() => setPicking(true)}
              className="ml-auto shrink-0 px-2 py-0.5 rounded text-[11px] text-[var(--color-text-muted)] border border-white/10 hover:text-[var(--color-text)] hover:border-white/25"
            >
              Change…
            </button>
          </div>
          {data.configSource === 'backup.env' && (
            <p className="text-[var(--color-text-muted)] mt-1">
              Still coming from ops/backup.env — choosing a folder here moves it
              into Settings for good.
            </p>
          )}
          {saveConfig.isError && (
            <p className="text-red-400 mt-1">
              {(saveConfig.error as Error)?.message || 'Could not save.'}
            </p>
          )}
        </Row>
        <Row label="Latest snapshot">
          {data.snapshots.latest ?? 'none'}
          {data.snapshots.latestSizeBytes != null && (
            <span className="text-[var(--color-text-muted)]">
              {' · '}
              {formatBytes(data.snapshots.latestSizeBytes)}
            </span>
          )}
        </Row>
        <Row label="Snapshots">
          <div className="flex items-center gap-2">
            <span>{describeSnapshots(data)}</span>
            <label className="ml-auto flex items-center gap-1 text-[var(--color-text-muted)]">
              keep
              <input
                type="number"
                min={1}
                max={365}
                aria-label="Retention days"
                defaultValue={data.retentionDays}
                onBlur={e => {
                  const days = Number(e.target.value);
                  if (days !== data.retentionDays && days >= 1 && days <= 365) {
                    saveConfig.mutate({ retentionDays: days });
                  }
                }}
                className="w-14 px-1 py-0.5 rounded bg-black/20 border border-white/10 text-[var(--color-text)]"
              />
              days
            </label>
          </div>
        </Row>
        <Row label="Media mirror">
          {data.media.lastModified
            ? `updated ${new Date(data.media.lastModified).toLocaleDateString()}`
            : 'nothing mirrored yet'}
        </Row>
        {used != null && (
          <Row label="Drive space">
            {formatBytes(data.mount.freeBytes)} free of{' '}
            {formatBytes(data.mount.totalBytes)}
            <div className="mt-1 h-1 rounded bg-white/10 overflow-hidden max-w-xs">
              <div
                className="h-full bg-[var(--color-primary)]"
                style={{ width: `${Math.round(used * 100)}%` }}
              />
            </div>
          </Row>
        )}
        <Row label="Next run">
          {data.timer.nextRun
            ? new Date(data.timer.nextRun).toLocaleString()
            : 'unknown'}
          {data.timer.available && data.timer.enabled === false && (
            <span className="text-red-400">{' · timer disabled'}</span>
          )}
        </Row>
      </dl>

      {(data.run.output || runNow.isError) && (
        <div>
          <p className="text-xs text-[var(--color-text-muted)] mb-1">
            {runNow.isError
              ? 'Could not start the backup.'
              : data.run.ok
                ? 'Last manual run succeeded:'
                : 'Last manual run failed:'}
          </p>
          {data.run.output && (
            <pre className="text-[11px] leading-relaxed text-[var(--color-text-muted)] bg-black/20 rounded p-2 overflow-x-auto max-h-48 whitespace-pre-wrap">
              {data.run.output}
            </pre>
          )}
        </div>
      )}

      <p className="text-xs text-[var(--color-text-muted)]">
        Full history: <code>journalctl --user -u lunaschal-backup</code>
      </p>

      {picking && (
        <FolderPicker
          initialPath={data.destination ?? '/'}
          onClose={() => setPicking(false)}
          onSelect={path => {
            setPicking(false);
            saveConfig.mutate({ destination: path });
          }}
        />
      )}
    </div>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <>
      <dt className="text-[var(--color-text-muted)] whitespace-nowrap">
        {label}
      </dt>
      <dd className="text-[var(--color-text)] min-w-0">{children}</dd>
    </>
  );
}
