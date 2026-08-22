import { useRef, useState } from 'react';
import { ulid } from '../../lib/ulid';
import { useIdeaCreate } from '../../offline/mutationDefaults';
import { useRecorder } from '../../hooks/useRecorder';
import { useShortcutScope } from '../../shortcuts/ShortcutProvider';

interface IdeaCaptureProps {
  onCreated: (id: string) => void;
}

/**
 * Capture box at the top of the list pane. Dictation appends to the textarea
 * rather than submitting straight away (the BrainDump pattern) so a transcript
 * can be corrected, or two thoughts recorded into one idea, before saving.
 */
export function IdeaCapture({ onCreated }: IdeaCaptureProps) {
  const [text, setText] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const recorder = useRecorder(transcript =>
    setText(prev => (prev ? `${prev}\n${transcript}` : transcript))
  );

  // Queued, not posted: an idea captured with no backend in reach is still an
  // idea. The id is minted here so the optimistic row and the eventual server
  // row are the same row, and `onCreated` can open it before it has been sent.
  const create = useIdeaCreate();

  const toggleRecording = () => {
    if (recorder.status === 'recording') recorder.stop();
    else if (recorder.status === 'idle') recorder.start();
  };

  useShortcutScope(1, {
    create: () => textareaRef.current?.focus(),
    record: toggleRecording,
  });

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || create.isPending) return;
    const id = ulid();
    create.mutate({ id, rawContent: trimmed });
    setText('');
    onCreated(id);
  };

  const busy = recorder.status !== 'idle';

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
        placeholder="Capture an idea — type it, or hit record and talk."
        className="w-full resize-none rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1.5 text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
      />
      <div className="flex items-center gap-2 mt-2">
        <button
          type="button"
          onClick={toggleRecording}
          disabled={
            recorder.status === 'transcribing' || !recorder.canTranscribe
          }
          title={
            recorder.canTranscribe
              ? undefined
              : 'Offline — dictation needs the server'
          }
          aria-label={
            recorder.status === 'recording'
              ? 'Stop recording'
              : 'Record an idea'
          }
          className={`px-2 py-1 rounded text-sm ${
            recorder.status === 'recording'
              ? 'bg-red-500/25 text-red-300'
              : 'bg-white/10 text-[var(--color-text)] hover:bg-white/15'
          } disabled:opacity-50`}
        >
          {recorder.status === 'recording'
            ? '■ Stop'
            : recorder.status === 'transcribing'
              ? 'Transcribing…'
              : '● Record'}
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={!text.trim() || create.isPending || busy}
          className="px-2 py-1 rounded text-sm bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-40"
        >
          {create.isPending ? 'Saving…' : 'Save idea'}
        </button>
      </div>
      {recorder.error && (
        <p className="mt-2 text-xs text-red-400">{recorder.error}</p>
      )}
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
