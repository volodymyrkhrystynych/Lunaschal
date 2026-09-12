import { RecordingButton } from '../RecordingButton';
import { useRef, useState } from 'react';
import { useIdeaCreate } from '../../offline/mutationDefaults';
import { useClipStage } from '../../hooks/useClipStage';
import { ClipStrip } from '../ClipRecorder';
import { useShortcutScope } from '../../shortcuts/ShortcutProvider';

interface IdeaCaptureProps {
  onCreated: (id: string) => void;
  /**
   * Which repo to file the new idea under. Undefined means "let the server
   * decide" — it stamps the registered default, so a single-repo setup never
   * has to say so.
   */
  repoId?: string;
}

/**
 * Capture box at the top of the list pane: type an idea, record one, or both.
 *
 * Recording stages a clip and nothing else — stop, think, record again, and
 * Save sends the lot: the typed line and every clip, under one idea. The idea's
 * id is minted at the first chunk, so a phone that dies mid-thought still files
 * the audio as *this* idea rather than as a stray journal entry.
 *
 * It has been through both extremes. Dictation used to append to the textarea
 * and block on the transcription, which is fine at a desk and wrong everywhere
 * an idea actually turns up; then stopping the recording *was* the save, which
 * fixed the waiting but allowed exactly one clip and no words beside it.
 */
export function IdeaCapture({ onCreated, repoId }: IdeaCaptureProps) {
  const [text, setText] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Queued, not posted: an idea captured with no backend in reach is still an
  // idea. The id is minted client-side so the optimistic row and the eventual
  // server row are the same row, and `onCreated` can open it before it has
  // been sent.
  const create = useIdeaCreate();
  const clips = useClipStage({ kind: 'idea', ...(repoId ? { repoId } : {}) });

  useShortcutScope(1, {
    create: () => textareaRef.current?.focus(),
    record: clips.toggle,
  });

  const submit = () => {
    const trimmed = text.trim();
    if ((!trimmed && clips.clips.length === 0) || create.isPending) return;
    // Whatever the clips were recorded under, so the idea the transcripts are
    // delivered to is the one this create makes.
    const id = clips.claimId();
    create.mutate({
      id,
      // An idea that is only spoken starts empty; the transcript fills it in,
      // exactly as it did when stopping was the save.
      rawContent: trimmed,
      ...(repoId ? { repoId } : {}),
    });
    void clips.commit();
    setText('');
    clips.reset();
    onCreated(id);
  };

  return (
    <div className="p-3 border-b border-white/10 shrink-0">
      <textarea
        ref={textareaRef}
        data-idea-capture
        value={text}
        onChange={e => setText(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            submit();
          }
        }}
        rows={3}
        placeholder="Capture an idea — type it, or hit Transcribe and talk."
        className="w-full resize-none rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1.5 text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
      />
      <div className="flex items-center gap-2 mt-2">
        <RecordingButton
          status={clips.status}
          starting={clips.starting}
          onClick={clips.toggle}

          testId="idea-capture-record"
        />
        <button
          type="button"
          onClick={submit}
          // Never gated on the recorder: a stopped clip is staged, and a clip
          // still recording is stopped by its own button.
          disabled={
            (!text.trim() && clips.clips.length === 0) || create.isPending
          }
          className="px-2 py-1 rounded text-sm bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-40"
        >
          {create.isPending ? 'Saving…' : 'Save idea'}
        </button>
        {clips.recording && (
          <span className="text-xs text-[var(--color-text-muted)]">
            Stop when you pause — you can add another clip.
          </span>
        )}
      </div>
      <ClipStrip stage={clips} testId="idea-capture" />
      {create.isError && (
        <p className="mt-2 text-xs text-red-400">
          {create.error instanceof Error
            ? create.error.message
            : 'Could not save the idea'}
        </p>
      )}
    </div>
  );
}
