import { useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { api, type ResumeImportPreview } from '@/hooks/api';
import { importSummary } from '@/lib/jobs';

type Preview = ResumeImportPreview;

/**
 * Read an existing resume instead of typing one.
 *
 * Nothing in the Jobs tab works before the profile exists — tailoring has no
 * bullets and the feed cannot score, so every posting sorts as unscored. This
 * is the shortest path from "installed" to "usable".
 *
 * It previews rather than writing: the bullets it creates become the evidence
 * every future resume is generated from, so they get looked at once before
 * they are believed. Deselecting is how you drop a line the parser misread —
 * everything here is checkboxes over what was found, never free text the model
 * wrote (it never wrote any; bullet text comes verbatim from the document).
 */
export function ProfileImport({ onImported }: { onImported: () => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [dropped, setDropped] = useState<Set<string>>(new Set());
  const fileInput = useRef<HTMLInputElement>(null);

  const read = useMutation({
    mutationFn: (input: File | string) =>
      typeof input === 'string'
        ? api.jobs.profile.importText(input)
        : api.jobs.profile.importFile(input),
    onSuccess: result => {
      setPreview(result);
      setDropped(new Set());
    },
  });

  const commit = useMutation({
    mutationFn: () => api.jobs.profile.commitImport(selected()),
    onSuccess: () => {
      setPreview(null);
      setText('');
      setOpen(false);
      onImported();
    },
  });

  const isDropped = (key: string) => dropped.has(key);
  const toggle = (key: string) =>
    setDropped(current => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  /** What the checkboxes currently add up to. */
  function selected(): Partial<Preview> {
    if (!preview) return {};
    return {
      contact: preview.contact,
      roles: preview.roles
        .map((role, r) => ({
          ...role,
          bullets: role.bullets.filter((_, b) => !isDropped(`b${r}.${b}`)),
        }))
        .filter((_, r) => !isDropped(`r${r}`)),
      skills: preview.skills.filter((_, s) => !isDropped(`s${s}`)),
      education: preview.education.filter((_, e) => !isDropped(`e${e}`)),
    };
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="min-h-[44px] px-4 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10"
      >
        Import from a resume
      </button>
    );
  }

  const summary = preview ? importSummary(selected() as Preview) : null;

  return (
    <div className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3 space-y-3">
      {!preview && (
        <>
          <p className="text-xs text-[var(--color-text-muted)]">
            Pick a <b>.docx</b>, or paste the text. Nothing is saved until you
            review it.
          </p>
          <input
            ref={fileInput}
            type="file"
            accept=".docx"
            onChange={e => {
              const file = e.target.files?.[0];
              if (file) read.mutate(file);
            }}
            className="block w-full text-xs text-[var(--color-text-muted)] file:mr-3 file:min-h-[36px] file:px-3 file:rounded file:border file:border-white/20 file:bg-white/5 file:text-[var(--color-text)]"
          />
          <textarea
            value={text}
            onChange={e => setText(e.target.value)}
            rows={5}
            placeholder="…or paste your resume text here"
            className="w-full p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm text-[var(--color-text)] resize-y"
          />
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => read.mutate(text)}
              disabled={!text.trim() || read.isPending}
              className="min-h-[44px] px-4 rounded text-sm bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40 disabled:opacity-50"
            >
              {read.isPending ? 'Reading…' : 'Read it'}
            </button>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="min-h-[44px] px-4 rounded text-sm border border-white/20 bg-white/5"
            >
              Cancel
            </button>
          </div>
        </>
      )}

      {read.isError && (
        <p className="text-sm text-red-400">{(read.error as Error).message}</p>
      )}

      {preview && (
        <>
          <p className="text-xs text-[var(--color-text-muted)]">
            Untick anything it got wrong. Bullet text is exactly what your
            document said — it was not rewritten.
          </p>

          {preview.roles.map((role, r) => (
            <div
              key={r}
              className={`rounded border p-2 space-y-1 ${
                isDropped(`r${r}`)
                  ? 'border-white/5 opacity-40'
                  : 'border-white/10'
              }`}
            >
              <label className="flex items-start gap-2 text-sm text-[var(--color-text)]">
                <input
                  type="checkbox"
                  checked={!isDropped(`r${r}`)}
                  onChange={() => toggle(`r${r}`)}
                  className="mt-1"
                />
                <span>
                  {role.title || '(no title)'}
                  {role.company && ` · ${role.company}`}
                  {(role.startLabel || role.endLabel) && (
                    <span className="text-[var(--color-text-muted)]">
                      {' '}
                      · {role.startLabel}
                      {role.endLabel && `–${role.endLabel}`}
                    </span>
                  )}
                </span>
              </label>
              {role.bullets.map((bullet, b) => (
                <label
                  key={b}
                  className="flex items-start gap-2 text-xs text-[var(--color-text-muted)] pl-5"
                >
                  <input
                    type="checkbox"
                    checked={!isDropped(`b${r}.${b}`)}
                    onChange={() => toggle(`b${r}.${b}`)}
                    className="mt-0.5"
                  />
                  <span>{bullet.text}</span>
                </label>
              ))}
            </div>
          ))}

          {preview.skills.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {preview.skills.map((skill, s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => toggle(`s${s}`)}
                  className={`min-h-[36px] px-2 rounded text-xs border ${
                    isDropped(`s${s}`)
                      ? 'border-white/10 text-[var(--color-text-muted)] line-through'
                      : 'border-[var(--color-primary)]/40 bg-[var(--color-primary)]/10 text-[var(--color-primary)]'
                  }`}
                >
                  {skill}
                </button>
              ))}
            </div>
          )}

          {preview.education.map((entry, e) => (
            <label
              key={e}
              className="flex items-start gap-2 text-xs text-[var(--color-text)]"
            >
              <input
                type="checkbox"
                checked={!isDropped(`e${e}`)}
                onChange={() => toggle(`e${e}`)}
                className="mt-0.5"
              />
              <span>
                {entry.credential} {entry.field && `· ${entry.field}`}{' '}
                {entry.institution && `· ${entry.institution}`}
              </span>
            </label>
          ))}

          {preview.unusedLines.length > 0 && (
            <details className="text-xs text-[var(--color-text-muted)]">
              <summary className="cursor-pointer min-h-[36px]">
                {preview.unusedLines.length} lines it did not place
              </summary>
              <ul className="mt-1 space-y-0.5 pl-4 list-disc">
                {preview.unusedLines.map(line => (
                  <li key={line.index}>{line.text}</li>
                ))}
              </ul>
            </details>
          )}

          {commit.isError && (
            <p className="text-sm text-red-400">
              {(commit.error as Error).message}
            </p>
          )}

          <div className="flex gap-2 items-center flex-wrap">
            <button
              type="button"
              onClick={() => commit.mutate()}
              disabled={commit.isPending || summary?.roles === 0}
              className="min-h-[44px] px-4 rounded text-sm bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40 disabled:opacity-50"
            >
              {commit.isPending ? 'Adding…' : 'Add to profile'}
            </button>
            <button
              type="button"
              onClick={() => setPreview(null)}
              className="min-h-[44px] px-4 rounded text-sm border border-white/20 bg-white/5"
            >
              Start over
            </button>
            {summary && (
              <span className="text-xs text-[var(--color-text-muted)]">
                {summary.label}
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
