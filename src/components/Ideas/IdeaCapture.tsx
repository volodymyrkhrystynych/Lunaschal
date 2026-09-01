import { useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ulid } from '../../lib/ulid';
import { useIdeaCreate } from '../../offline/mutationDefaults';
import { useRecorder } from '../../hooks/useRecorder';
import { captureIdeaRecording } from '../../offline/recordingQueue';
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
 * Capture box at the top of the list pane: type an idea, or record one.
 *
 * The two halves are deliberately different. Typing is a small edit loop —
 * write, correct, Save. Recording is the Journal button's contract instead:
 * **stopping the recording is the save**. The clip goes to the durable store
 * and is uploaded as a journal entry carrying the idea's id, the idea appears
 * in the list immediately with no text in it, and the transcript, the cleanup
 * and the title all arrive minutes later on their own.
 *
 * It used to work the other way — dictation appended to the textarea and you
 * pressed Save — which is fine at a desk and wrong everywhere an idea actually
 * turns up. It meant holding the thought while the transcription ran, then
 * needing a second deliberate action; anything that interrupted the phone in
 * between lost the recording outright, because it only ever existed in memory.
 */
export function IdeaCapture({ onCreated, repoId }: IdeaCaptureProps) {
  const [text, setText] = useState('');
  const [notice, setNotice] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const qc = useQueryClient();

  // Queued, not posted: an idea captured with no backend in reach is still an
  // idea. The id is minted here so the optimistic row and the eventual server
  // row are the same row, and `onCreated` can open it before it has been sent.
  const create = useIdeaCreate();

  const recorder = useRecorder(
    // No transcript comes back here: in durable mode the audio is uploaded and
    // the server transcribes it into both the entry and the idea.
    () => undefined,
    undefined,
    {
      onNotice: setNotice,
      onRecording: rec => {
        // Not awaited — see captureIdeaRecording. The idea is already in the
        // list from the optimistic insert, and the audio is on disk until the
        // server confirms it, so there is nothing left to wait for.
        void captureIdeaRecording(qc, rec).catch(() => undefined);
        if (rec.idea) onCreated(rec.idea.id);
      },
    }
  );

  const toggleRecording = () => {
    if (recorder.status === 'recording') {
      recorder.stop();
      return;
    }
    if (recorder.status !== 'idle') return;
    setNotice('');
    // The idea's id is minted before the first chunk and stored beside the
    // audio, so a phone that dies mid-recording still knows, on the way back
    // up, that this clip was an idea.
    void recorder.start('transcribe', {
      durable: true,
      idea: { id: ulid(), ...(repoId ? { repoId } : {}) },
    });
  };

  useShortcutScope(1, {
    create: () => textareaRef.current?.focus(),
    record: toggleRecording,
  });

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || create.isPending) return;
    const id = ulid();
    create.mutate({ id, rawContent: trimmed, ...(repoId ? { repoId } : {}) });
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
          // Gated only while the recorder is finishing one off: recording works
          // offline (the clip is stored and uploaded later), and while
          // recording this button is the only way to stop.
          disabled={recorder.status === 'saving'}
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
          {recorder.status === 'recording' ? '■ Stop' : '● Record'}
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={!text.trim() || create.isPending || busy}
          className="px-2 py-1 rounded text-sm bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-40"
        >
          {create.isPending ? 'Saving…' : 'Save idea'}
        </button>
        {recorder.status === 'recording' && (
          <span className="text-xs text-[var(--color-text-muted)]">
            Stop to save — it transcribes itself.
          </span>
        )}
      </div>
      {notice && (
        <p className="mt-2 text-xs text-[var(--color-text-muted)]">{notice}</p>
      )}
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
