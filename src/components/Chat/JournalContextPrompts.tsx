import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type JournalEntry } from '../../hooks/api';
import { useClipStage } from '../../hooks/useClipStage';
import { ClipRecorder } from '../ClipRecorder';
import { Modal } from '../Modal';
import { ImageLightbox, useLightbox } from '../ImageLightbox';

const queryKey = ['journal', 'contextPrompts'];
const buttonClass =
  'rounded px-3 py-1.5 text-sm hover:bg-white/10 disabled:opacity-40';
const entryTime = (entry: JournalEntry) =>
  entry.createdAt ? new Date(entry.createdAt).toLocaleString() : 'Unknown date';

function ContextModal({
  entry,
  onClose,
  onDone,
}: {
  entry: JournalEntry;
  onClose: () => void;
  onDone: (uploads: Promise<void>) => void;
}) {
  const [text, setText] = useState('');
  const clips = useClipStage({ kind: 'journal', entryId: entry.id });
  const lightbox = useLightbox();
  const qc = useQueryClient();
  const recording = clips.recording || clips.busy;
  const save = useMutation({
    mutationFn: async () => {
      // Save text before uploading clips so a fast transcript cannot race it.
      if (text.trim())
        await api.journal.resolveContextPrompt(entry.id, 'save', text.trim());
    },
    onSuccess: () => {
      const uploads = clips.commit();
      clips.reset();
      qc.invalidateQueries({ queryKey: ['journal'] });
      onDone(uploads);
    },
  });

  const close = () => {
    if (lightbox.src) {
      lightbox.close();
      return;
    }
    if (recording || save.isPending) return;
    // As in the journal editor, closing never silently discards stored audio.
    void clips.commit().finally(() => qc.invalidateQueries({ queryKey }));
    onClose();
  };

  return (
    <Modal
      onClose={close}
      role="dialog"
      ariaLabel="Add journal context"
      closeOnBackdropClick={false}
      className="bg-[var(--color-surface)] text-[var(--color-text)] rounded-lg border border-white/10 max-w-2xl w-full max-h-[90dvh] overflow-y-auto p-4 space-y-4"
    >
      <div className="flex justify-between items-start gap-2">
        <div>
          <h2 className="font-medium">
            {entry.title || 'Add journal context'}
          </h2>
          <p className="text-xs text-[var(--color-text-muted)]">
            {entryTime(entry)}
          </p>
        </div>
        <button
          className={buttonClass}
          onClick={close}
          disabled={recording || save.isPending}
        >
          Close
        </button>
      </div>
      <div className="flex flex-wrap gap-3">
        {(entry.attachments ?? []).map(a =>
          a.kind === 'image' ? (
            <button
              key={a.id}
              type="button"
              onClick={() => lightbox.open(a.url ?? null)}
              aria-label={`Enlarge ${a.name}`}
            >
              <img
                src={a.url}
                alt={a.name}
                className="max-h-64 max-w-full rounded object-contain"
              />
            </button>
          ) : (
            <div key={a.id} className="w-full space-y-1">
              <p className="text-sm">{a.name}</p>
              <audio
                controls
                preload="none"
                src={a.url}
                aria-label={a.name}
                className="w-full"
              />
            </div>
          )
        )}
      </div>
      <label className="block text-sm">
        What was happening?
        <textarea
          autoFocus
          value={text}
          onChange={e => setText(e.target.value)}
          rows={4}
          className="mt-2 w-full bg-[var(--color-bg)] border border-white/20 rounded p-2"
          placeholder="Add context to this journal entry…"
        />
      </label>
      <ClipRecorder stage={clips} />
      <p className="text-xs text-[var(--color-text-muted)]">
        Recordings are attached and transcribed into this entry. Its original
        date stays the same.
      </p>
      {save.isError && (
        <p role="alert" className="text-sm text-red-400">
          {save.error.message}
        </p>
      )}
      <div className="flex justify-end">
        <button
          className={`${buttonClass} bg-[var(--color-primary)]/20 text-[var(--color-primary)]`}
          disabled={
            save.isPending ||
            recording ||
            (!text.trim() && clips.clips.length === 0)
          }
          onClick={() => save.mutate()}
        >
          {save.isPending ? 'Saving…' : 'Save context'}
        </button>
      </div>
      <ImageLightbox src={lightbox.src} onClose={lightbox.close} />
    </Modal>
  );
}

/** Daily media prompts alongside chat's other suggestion cards. */
export function JournalContextPrompts() {
  const qc = useQueryClient();
  const [open, setOpen] = useState<JournalEntry | null>(null);
  const [submitted, setSubmitted] = useState<Set<string>>(() => new Set());
  const {
    data = [],
    isError,
    refetch,
  } = useQuery({
    queryKey,
    queryFn: api.journal.contextPrompts,
    refetchInterval: 60_000,
  });
  const dismiss = useMutation({
    mutationFn: (id: string) => api.journal.resolveContextPrompt(id, 'dismiss'),
    onSuccess: () => qc.invalidateQueries({ queryKey }),
  });
  const entries = data.filter(e => !submitted.has(e.id));
  if (!entries.length && !open && !isError) return null;

  return (
    <section aria-label="Journal context suggestions" className="space-y-2">
      <h3 className="text-sm font-medium">Add context to your journal</h3>
      {isError && (
        <button className={buttonClass} onClick={() => void refetch()}>
          Could not load journal prompts. Retry
        </button>
      )}
      {entries.map(entry => (
        <div
          key={entry.id}
          className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3"
        >
          <p className="text-sm">{entry.title || 'Untitled media entry'}</p>
          <p className="text-xs text-[var(--color-text-muted)]">
            {entryTime(entry)} · {entry.attachments?.length ?? 0} attachments
          </p>
          <div className="flex justify-end gap-2 mt-2">
            <button
              className={buttonClass}
              disabled={dismiss.isPending}
              onClick={() => dismiss.mutate(entry.id)}
            >
              Dismiss permanently
            </button>
            <button
              className={`${buttonClass} text-[var(--color-primary)]`}
              onClick={() => setOpen(entry)}
            >
              Add context
            </button>
          </div>
          {dismiss.isError && dismiss.variables === entry.id && (
            <p role="alert" className="text-sm text-red-400">
              {dismiss.error.message}
            </p>
          )}
        </div>
      ))}
      {open && (
        <ContextModal
          key={open.id}
          entry={open}
          onClose={() => setOpen(null)}
          onDone={uploads => {
            const id = open.id;
            setSubmitted(previous => new Set(previous).add(id));
            setOpen(null);
            // Hide while the durable upload is pending. If transcription/upload
            // fails, eligibility makes the prompt available again for another try.
            void uploads.finally(async () => {
              await qc.invalidateQueries({ queryKey });
              setSubmitted(previous => {
                const next = new Set(previous);
                next.delete(id);
                return next;
              });
            });
          }}
        />
      )}
    </section>
  );
}
