import type { ChatAttachment } from '../../hooks/api';

/**
 * The voice clips a chat message was spoken into.
 *
 * Kept out of the photo strip for the reason `Food/MealClips.tsx` keeps them out
 * of that one: an `<audio controls>` bar has no thumbnail and no business being
 * laid out beside pictures.
 *
 * There is no transcript shown under the clip, and that is the difference from
 * the meal version. A meal clip's transcript is folded into a note beside other
 * fields, so it needs somewhere of its own to be read; here the transcript *is*
 * the message, rendered in the bubble a line below. Printing it twice would be
 * the same words in the same box.
 */
export function ChatClips({ clips }: { clips: ChatAttachment[] }) {
  if (clips.length === 0) return null;

  return (
    <div className="mb-2 space-y-1">
      {clips.map(clip => (
        <div key={clip.id}>
          <audio
            src={clip.url}
            controls
            preload="none"
            className="w-full h-8 min-w-[12rem]"
          />
          {clip.transcriptStatus === 'running' && (
            <p className="mt-1 text-xs opacity-70">Transcribing…</p>
          )}
          {clip.transcriptStatus === 'error' && (
            // Worth saying plainly: the recording is safe and playable right
            // above this line, so what failed is the words, not the message.
            <p className="mt-1 text-xs opacity-90">
              Couldn&apos;t transcribe this
              {clip.transcriptError ? ` — ${clip.transcriptError}` : ''}. The
              recording is saved.
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

/** The attachments that belong in the photo strip — i.e. not a voice clip. */
export function photoAttachments(
  attachments: ChatAttachment[] | undefined
): ChatAttachment[] {
  return (attachments ?? []).filter(a => a.kind !== 'audio');
}

/** The attachments that are voice clips. */
export function clipAttachments(
  attachments: ChatAttachment[] | undefined
): ChatAttachment[] {
  return (attachments ?? []).filter(a => a.kind === 'audio');
}
