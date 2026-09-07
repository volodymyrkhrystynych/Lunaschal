import type { FoodMedia } from '../../hooks/api';
import { CollapsibleText } from '../CollapsibleText';

/**
 * The voice clips on a meal, with whatever was said in them.
 *
 * Kept out of the photo strip both places render: a `<audio controls>` bar has
 * no thumbnail and no business being scrolled horizontally beside pictures of
 * the plate. Shared by the Food log and the meal card in the Journal feed, so
 * a clip looks and behaves the same in both.
 */
export function MealClips({ media }: { media: FoodMedia[] }) {
  const clips = media.filter(m => m.kind === 'audio');
  if (clips.length === 0) return null;

  return (
    <div className="space-y-2 mb-3">
      {clips.map(clip => (
        <div key={clip.id} className="space-y-1">
          <audio
            src={clip.url}
            controls
            preload="none"
            className="w-full h-8"
          />
          {clip.transcriptStatus === 'running' && (
            <p className="text-xs text-[var(--color-text-muted)]">
              Transcribing…
            </p>
          )}
          {clip.transcriptStatus === 'error' && clip.transcriptError && (
            <p className="text-xs text-red-400">{clip.transcriptError}</p>
          )}
          {clip.transcript && (
            // Same rule as a journal attachment's: readable once, out of the
            // way afterwards. The key is the clip's id, so the two never share
            // an "already seen" flag.
            <CollapsibleText
              storageKey={`food-media-transcript-seen:${clip.id}`}
              label="Transcript"
            >
              {clip.transcript}
            </CollapsibleText>
          )}
        </div>
      ))}
    </div>
  );
}

/** Everything that belongs in the photo strip — i.e. not a voice clip. */
export function visualMedia(media: FoodMedia[]): FoodMedia[] {
  return media.filter(m => m.kind !== 'audio');
}
