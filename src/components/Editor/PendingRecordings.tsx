import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  assembleBlob,
  deleteRecording,
  listRecordings,
  markAttempt,
  type StoredRecording,
} from '../../offline/recordingStore';
import { enqueueRecordingUpload } from '../../offline/recordingQueue';
import { recordingFilename } from '../../lib/journalAttachments';
import { pendingRecordingLabel, visibleRecordings } from '../../lib/recordings';

/**
 * Journal audio still on the device.
 *
 * Deliberately quiet: a recording that uploads on the first try never appears
 * here at all, and one that is merely waiting for the backend to come back is
 * shown without alarm. It exists for the case the whole feature is about — the
 * upload didn't work and the audio is sitting on the phone — so that "where did
 * my journal entry go" has an answer on screen instead of in a console log.
 */
export function PendingRecordings() {
  const qc = useQueryClient();
  const [all, setAll] = useState<StoredRecording[]>([]);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      const list = await listRecordings();
      if (!cancelled) setAll(list);
    };
    void refresh();
    // Polled rather than pushed: the store is written from the recorder, the
    // upload queue and the startup sweep, and a 2 s poll is far less machinery
    // than making all three publish events.
    const timer = setInterval(() => void refresh(), 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const rows = visibleRecordings(all);
  if (rows.length === 0) return null;

  const retry = (rec: StoredRecording) => {
    void markAttempt(rec.id, null, false).then(() =>
      // `rec.idea` rather than nothing: a retried Ideas capture is still an
      // idea, and re-filing it as a plain journal entry would lose the half the
      // user was waiting for.
      enqueueRecordingUpload(qc, rec.id, undefined, { idea: rec.idea }).catch(
        () => undefined
      )
    );
  };

  const download = async (rec: StoredRecording) => {
    const blob = await assembleBlob(rec.id);
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = recordingFilename(rec.mimeType);
    a.click();
    URL.revokeObjectURL(url);
  };

  const discard = (rec: StoredRecording) => {
    // The one path that destroys audio on purpose, so it asks first.
    if (!window.confirm('Delete this recording? It has not been saved yet.')) {
      return;
    }
    void deleteRecording(rec.id).then(() =>
      setAll(list => list.filter(r => r.id !== rec.id))
    );
  };

  return (
    <div className="px-3 py-1.5 border-t border-[var(--color-border)] bg-amber-500/10 text-xs">
      {rows.map(rec => (
        <div key={rec.id} className="flex items-center gap-2 py-0.5">
          <span className="w-2 h-2 rounded-full shrink-0 bg-amber-400" />
          <span className="truncate text-[var(--color-text)]">
            {pendingRecordingLabel(rec)}
          </span>
          <span className="grow" />
          <button
            onClick={() => retry(rec)}
            className="shrink-0 px-2 py-0.5 rounded bg-white/10 hover:bg-white/20"
          >
            Retry
          </button>
          <button
            onClick={() => void download(rec)}
            className="shrink-0 px-2 py-0.5 rounded bg-white/10 hover:bg-white/20"
          >
            Download
          </button>
          <button
            onClick={() => discard(rec)}
            className="shrink-0 px-2 py-0.5 rounded bg-white/10 hover:bg-white/20 text-red-300"
          >
            Discard
          </button>
        </div>
      ))}
    </div>
  );
}
