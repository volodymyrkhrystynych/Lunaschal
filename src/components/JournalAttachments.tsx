import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { type JournalAttachment, api } from '../hooks/api';
import { useAttachmentUpload } from '../hooks/useAttachmentUpload';
import { ImageLightbox, useLightbox } from './ImageLightbox';
import { mapLink } from '../lib/food';
import {
  ACCEPT_AUDIO,
  ACCEPT_IMAGE,
  canDescribeAudio,
  canTranscribe,
  describeAudioLabel,
  filesFromTransfer,
  formatBytes,
  hasRunningTranscription,
  rejectedFilesMessage,
  summarizeAttachments,
  transcribeLabel,
} from '../lib/journalAttachments';

/**
 * Audio, video and photo attachments on a journal entry.
 *
 * Shown expanded, inline with the entry, rather than folded behind a click —
 * a voice memo or photo is part of what was recorded that day. The transcript
 * (speech-to-text) stays opt-in, produced only when asked for, because on this
 * hardware it costs a real CPU transcription; the non-speech audio/video
 * description auto-fires on upload instead (backend/routes/journal.py), since
 * it already runs as a background job and costs nothing to start eagerly.
 *
 * `editable` is what the Journal view's edit mode toggles: adding, renaming and
 * deleting need it, while playing a recording or reading a transcript does not.
 *
 * `children` (the entry's title/body fields) are rendered above the attachment
 * list and inside the same paste/drop target, so a voice memo copied on a phone
 * can be pasted straight into the editor rather than saved to Files first and
 * picked back out.
 */
export function JournalAttachments({
  entryId,
  attachments,
  editable,
  children,
}: {
  entryId: string;
  attachments: JournalAttachment[] | undefined;
  editable: boolean;
  children?: React.ReactNode;
}) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const audioInputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const list = attachments ?? [];

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['journal'] });

  const {
    uploadFiles,
    isUploading,
    error: uploadError,
  } = useAttachmentUpload(entryId);

  const renameAttachment = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) =>
      api.journal.attachments.rename(id, name),
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  });

  const deleteAttachment = useMutation({
    mutationFn: (id: string) => api.journal.attachments.delete(id),
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  });

  const transcribeAttachment = useMutation({
    mutationFn: (id: string) => api.journal.attachments.transcribe(id),
    onSuccess: () => {
      setError(null);
      invalidate();
    },
    onError: (e: Error) => setError(e.message),
  });

  const describeAudioAttachment = useMutation({
    mutationFn: (id: string) => api.journal.attachments.describeAudio(id),
    onSuccess: () => {
      setError(null);
      invalidate();
    },
    onError: (e: Error) => setError(e.message),
  });

  // The /events SSE stream already pushes a refetch when the background job
  // finishes, but it's a single long-lived connection that a sleeping Pocket 2
  // can drop silently. A slow poll while work is in flight is the belt to that
  // braces, and it stops the moment nothing is running.
  const running = hasRunningTranscription(list);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(invalidate, 4000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, entryId]);

  const pick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    // Reset first: picking the same file twice in a row otherwise fires no
    // change event, which reads as "the button stopped working".
    e.target.value = '';
    if (files.length) uploadFiles(files);
  };

  // Paste and drop are the whole point of this being a wrapper: on a phone or an
  // iPad, a voice memo or clip is copied from its own app, and making the user
  // export it to Files and pick it back out is the step worth deleting.
  const onPaste = (e: React.ClipboardEvent) => {
    if (!editable) return;
    const { accepted, rejected } = filesFromTransfer(e.clipboardData);
    if (!accepted.length) {
      // A paste that carried files we can't take says so; one carrying no files
      // at all is ordinary text and must still reach the textarea underneath.
      setError(rejectedFilesMessage(rejected));
      return;
    }
    e.preventDefault();
    setError(null);
    uploadFiles(accepted);
  };

  const onDrop = (e: React.DragEvent) => {
    if (!editable) return;
    const { accepted, rejected } = filesFromTransfer(e.dataTransfer);
    setIsDragging(false);
    if (!accepted.length) {
      setError(rejectedFilesMessage(rejected));
      return;
    }
    e.preventDefault();
    setError(null);
    uploadFiles(accepted);
  };

  // Safari gives no paste affordance unless the tap lands in an editable field,
  // and the keyboard route is awkward on a tablet — so an explicit button that
  // reads the clipboard on a user gesture. Best-effort: whether Safari exposes a
  // copied .m4a here at all is up to Safari, hence the plain-spoken failure.
  const pasteFromClipboard = async () => {
    setError(null);
    try {
      const items = await navigator.clipboard.read();
      const files: File[] = [];
      for (const item of items) {
        const type = item.types.find(
          t =>
            t.startsWith('audio/') ||
            t.startsWith('video/') ||
            t.startsWith('image/')
        );
        if (!type) continue;
        const blob = await item.getType(type);
        files.push(new File([blob], '', { type }));
      }
      if (!files.length) {
        setError('The clipboard has no audio, video or image in it.');
        return;
      }
      uploadFiles(files);
    } catch (e) {
      setError(
        `Couldn't read the clipboard: ${(e as Error).message || 'not permitted'}`
      );
    }
  };

  const onDragOver = (e: React.DragEvent) => {
    if (!editable || !e.dataTransfer?.types?.includes('Files')) return;
    // Without this the browser navigates away to the dropped file.
    e.preventDefault();
    setIsDragging(true);
  };

  const shown = error ?? uploadError;
  // Images and audio/video are laid out differently: a photo is small and
  // several fit side by side, while a recording's controls (rename,
  // transcribe, transcript text) need the full width to stay readable. Rather
  // than one full-width card per attachment regardless of kind — which used
  // to leave a small thumbnail with most of the row empty beside it — images
  // get their own wrapping strip and everything else keeps the vertical list.
  const images = list.filter(a => a.kind === 'image');
  const others = list.filter(a => a.kind !== 'image');
  const attachmentRow = (a: JournalAttachment) => (
    <AttachmentRow
      key={a.id}
      attachment={a}
      editable={editable}
      onRename={name => renameAttachment.mutate({ id: a.id, name })}
      onDelete={() => deleteAttachment.mutate(a.id)}
      onTranscribe={() => transcribeAttachment.mutate(a.id)}
      onDescribeAudio={() => describeAudioAttachment.mutate(a.id)}
    />
  );
  const body =
    list.length === 0 && !editable ? null : (
      <div className="mt-3">
        {list.length > 0 && (
          <div className="text-xs text-[var(--color-text-muted)] mb-2">
            Attachments — {summarizeAttachments(list)}
          </div>
        )}

        <div className="space-y-2">
          {shown && (
            <div className="px-3 py-2 bg-red-500/10 border border-red-500/20 rounded text-sm text-red-400">
              {shown}
            </div>
          )}

          {images.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {images.map(a => (
                <div key={a.id} className="w-56 shrink-0">
                  {attachmentRow(a)}
                </div>
              ))}
            </div>
          )}

          {others.map(attachmentRow)}

          {editable && (
            <div className="flex items-center gap-2 pt-1">
              <input
                ref={audioInputRef}
                type="file"
                accept={ACCEPT_AUDIO}
                onChange={pick}
                className="hidden"
                data-testid="journal-audio-input"
              />
              <input
                ref={imageInputRef}
                type="file"
                accept={ACCEPT_IMAGE}
                onChange={pick}
                className="hidden"
                data-testid="journal-image-input"
              />
              <button
                type="button"
                onClick={() => audioInputRef.current?.click()}
                disabled={isUploading}
                className="px-2 py-1 text-xs rounded border border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-white/20 disabled:opacity-50"
              >
                Add audio or video
              </button>
              <button
                type="button"
                onClick={() => imageInputRef.current?.click()}
                disabled={isUploading}
                className="px-2 py-1 text-xs rounded border border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-white/20 disabled:opacity-50"
              >
                Add photo
              </button>
              <button
                type="button"
                onClick={pasteFromClipboard}
                disabled={isUploading}
                className="px-2 py-1 text-xs rounded border border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-white/20 disabled:opacity-50"
              >
                Paste
              </button>
              {isUploading ? (
                <span className="text-xs text-[var(--color-text-muted)]">
                  Uploading…
                </span>
              ) : (
                <span className="text-xs text-[var(--color-text-muted)]">
                  …or drop a file anywhere in this entry
                </span>
              )}
            </div>
          )}
        </div>
      </div>
    );

  // Nothing to show and nothing to paste into: render the fields alone rather
  // than an empty wrapper.
  if (body === null) return <>{children}</>;

  // Otherwise the wrapper is the paste/drop target.
  return (
    <div
      onPaste={onPaste}
      onDrop={onDrop}
      onDragOver={onDragOver}
      onDragLeave={() => setIsDragging(false)}
      className={
        isDragging
          ? 'rounded outline-2 outline-dashed outline-[var(--color-primary)]'
          : undefined
      }
      data-testid="journal-attachment-dropzone"
    >
      {children}
      {body}
    </div>
  );
}

const KIND_ICONS: Record<JournalAttachment['kind'], string> = {
  audio: '🎙️',
  video: '🎬',
  image: '📷',
};

function AttachmentRow({
  attachment: a,
  editable,
  onRename,
  onDelete,
  onTranscribe,
  onDescribeAudio,
}: {
  attachment: JournalAttachment;
  editable: boolean;
  onRename: (name: string) => void;
  onDelete: () => void;
  onTranscribe: () => void;
  onDescribeAudio: () => void;
}) {
  const [name, setName] = useState(a.name);

  // Keep the field in step with the server after a refetch, but never while the
  // user is midway through typing a new name into it.
  useEffect(() => setName(a.name), [a.name]);

  const commitName = () => {
    const next = name.trim();
    if (!next || next === a.name) {
      setName(a.name);
      return;
    }
    onRename(next);
  };

  const lightbox = useLightbox();
  const geoLink = a.kind === 'image' ? mapLink(a.latitude, a.longitude) : null;

  return (
    <div className="rounded border border-white/10 bg-white/5 px-3 py-2 space-y-2">
      <div className="text-sm text-[var(--color-text)] flex items-center gap-2">
        <span aria-hidden="true">{KIND_ICONS[a.kind] ?? '📎'}</span>
        <span className="truncate">{a.name}</span>
        <span className="ml-auto text-xs text-[var(--color-text-muted)] shrink-0">
          {formatBytes(a.size)}
        </span>
      </div>

      {/* preload="none" throughout: an entry can carry several clips and the
          Pocket 2 should not fetch any of them until one is actually played. */}
      {a.kind === 'audio' && (
        <audio src={a.url} controls preload="none" className="w-full" />
      )}
      {a.kind === 'video' && (
        <video
          src={a.url}
          controls
          playsInline
          preload="none"
          className="w-full max-h-96 rounded bg-black"
        />
      )}
      {a.kind === 'image' && (
        <div className="flex items-center gap-2">
          {/* Fixed height, width follows the photo: a landscape shot in a
              portrait box was being cropped to its middle third. */}
          <button
            type="button"
            onClick={() => lightbox.open(a.url)}
            className="shrink-0 h-32 max-w-full rounded-md overflow-hidden border border-white/10 hover:border-[var(--color-primary)] transition-colors"
            title="View"
          >
            <img
              src={a.url}
              alt={a.name}
              loading="lazy"
              className="h-full w-auto max-w-full object-contain"
            />
          </button>
          {geoLink && (
            <a
              href={geoLink}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-[var(--color-text-muted)] underline hover:text-[var(--color-text)]"
            >
              🗺️ map
            </a>
          )}
        </div>
      )}
      <ImageLightbox src={lightbox.src} onClose={lightbox.close} />

      {editable && (
        <input
          value={name}
          onChange={e => setName(e.target.value)}
          onBlur={commitName}
          onKeyDown={e => {
            if (e.key === 'Enter') e.currentTarget.blur();
            if (e.key === 'Escape') setName(a.name);
          }}
          aria-label="What this attachment is about"
          placeholder="What is this about?"
          className="w-full bg-transparent text-sm text-[var(--color-text)] border border-white/10 rounded px-2 py-1 focus:outline-none focus:border-[var(--color-primary)]"
        />
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onTranscribe}
          disabled={!canTranscribe(a)}
          className="px-2 py-1 text-xs rounded border border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-white/20 disabled:opacity-50"
        >
          {transcribeLabel(a)}
        </button>
        {(a.kind === 'audio' || a.kind === 'video') && (
          <button
            type="button"
            onClick={onDescribeAudio}
            disabled={!canDescribeAudio(a)}
            className="px-2 py-1 text-xs rounded border border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-white/20 disabled:opacity-50"
          >
            {describeAudioLabel(a)}
          </button>
        )}
        <a
          href={a.url}
          download
          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          Download
        </a>
        {editable && (
          <button
            type="button"
            onClick={onDelete}
            className="ml-auto text-xs text-red-400 hover:text-red-300"
          >
            Delete
          </button>
        )}
      </div>

      {a.transcriptStatus === 'error' && a.transcriptError && (
        <div className="px-2 py-1 rounded bg-red-500/10 border border-red-500/20 text-xs text-red-400">
          {a.transcriptError}
        </div>
      )}

      {a.transcript && (
        <div className="px-3 py-2 bg-white/5 rounded text-sm text-[var(--color-text-muted)] whitespace-pre-wrap italic">
          {a.transcript}
        </div>
      )}

      {a.descriptionStatus === 'error' && a.descriptionError && (
        <div className="px-2 py-1 rounded bg-red-500/10 border border-red-500/20 text-xs text-red-400">
          {a.descriptionError}
        </div>
      )}

      {a.description && (
        <div className="px-3 py-2 bg-white/5 rounded text-sm text-[var(--color-text-muted)] whitespace-pre-wrap italic">
          {a.description}
        </div>
      )}
    </div>
  );
}
