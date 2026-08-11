import { useRef, useState, useEffect } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { useRecorder } from '../../hooks/useRecorder';
import { mediaKind } from '../../lib/food';

interface PickedMedia {
  file: File;
  url: string;
  kind: 'image' | 'video';
}

/** Best-effort device GPS; resolves null if unavailable or denied (never throws
 * so a log is never blocked by location). */
function currentPosition(): Promise<{
  latitude: number;
  longitude: number;
} | null> {
  return new Promise(resolve => {
    if (!navigator.geolocation) return resolve(null);
    navigator.geolocation.getCurrentPosition(
      pos =>
        resolve({
          latitude: pos.coords.latitude,
          longitude: pos.coords.longitude,
        }),
      () => resolve(null),
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 60000 }
    );
  });
}

/**
 * Phone-first food capture: a big text box (type or dictate via the mic) plus
 * buttons to attach photos/videos from the camera or library. Grabs the device
 * GPS on submit as a fallback — when a photo is attached the server prefers the
 * photo's own EXIF date and location. One multipart POST creates the entry; the
 * server keeps the full transcript and structures it (dish/place/rating +
 * optional recipe) in the background.
 */
export function FoodCapture({ onDone }: { onDone?: () => void }) {
  const [text, setText] = useState('');
  const [media, setMedia] = useState<PickedMedia[]>([]);
  const photoRef = useRef<HTMLInputElement>(null);
  const videoRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();

  const { status, error, start, stop } = useRecorder(t =>
    setText(prev => (prev ? `${prev} ${t}` : t))
  );
  const recording = status === 'recording';
  const transcribing = status === 'transcribing';

  // Revoke object URLs on unmount to avoid leaking blobs.
  useEffect(
    () => () => media.forEach(m => URL.revokeObjectURL(m.url)),
    [media]
  );

  const addFiles = (files: FileList | null) => {
    if (!files) return;
    const picked = Array.from(files).map(file => ({
      file,
      url: URL.createObjectURL(file),
      kind: mediaKind(file.type),
    }));
    setMedia(prev => [...prev, ...picked]);
  };

  const removeMedia = (url: string) => {
    setMedia(prev => {
      const gone = prev.find(m => m.url === url);
      if (gone) URL.revokeObjectURL(gone.url);
      return prev.filter(m => m.url !== url);
    });
  };

  const create = useMutation({
    mutationFn: async () => {
      const pos = await currentPosition();
      return api.food.create({
        text: text.trim() || undefined,
        media: media.map(m => m.file),
        latitude: pos?.latitude,
        longitude: pos?.longitude,
      });
    },
    onSuccess: () => {
      media.forEach(m => URL.revokeObjectURL(m.url));
      setText('');
      setMedia([]);
      queryClient.invalidateQueries({ queryKey: ['food'] });
      // The AI structuring finishes shortly after; refetch again to catch it.
      setTimeout(
        () => queryClient.invalidateQueries({ queryKey: ['food'] }),
        3000
      );
      onDone?.();
    },
  });

  const canSubmit = (text.trim() || media.length > 0) && !create.isPending;

  return (
    <div className="mb-4 p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
      <div className="relative">
        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          placeholder="What did you eat? Where? Was it good? (Describe how you made it and it'll be saved as a recipe.)"
          rows={4}
          className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none border border-white/10 rounded p-2 pr-12"
        />
        <button
          type="button"
          onClick={() => (recording ? stop() : void start())}
          disabled={transcribing}
          title={recording ? 'Stop recording' : 'Dictate'}
          className={`absolute top-2 right-2 w-9 h-9 rounded-full flex items-center justify-center transition-colors ${
            recording
              ? 'bg-red-500 text-white animate-pulse'
              : 'bg-white/10 text-[var(--color-text)] hover:bg-white/20'
          } disabled:opacity-50`}
        >
          {transcribing ? '…' : '🎤'}
        </button>
      </div>

      {error && (
        <div className="mt-2 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded text-sm text-red-400">
          {error}
        </div>
      )}

      {media.length > 0 && (
        <div className="flex flex-wrap gap-2 mt-3">
          {media.map(m => (
            <div key={m.url} className="relative w-20 h-20">
              {m.kind === 'video' ? (
                <video
                  src={m.url}
                  className="w-20 h-20 object-cover rounded border border-white/10"
                />
              ) : (
                <img
                  src={m.url}
                  alt=""
                  className="w-20 h-20 object-cover rounded border border-white/10"
                />
              )}
              <button
                onClick={() => removeMedia(m.url)}
                className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-black/70 text-white text-xs flex items-center justify-center hover:bg-black"
                title="Remove"
              >
                ×
              </button>
              {m.kind === 'video' && (
                <span className="absolute bottom-1 left-1 text-xs">🎥</span>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 mt-3">
        <input
          ref={photoRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          onChange={e => {
            addFiles(e.target.files);
            e.target.value = '';
          }}
        />
        <input
          ref={videoRef}
          type="file"
          accept="video/*"
          multiple
          className="hidden"
          onChange={e => {
            addFiles(e.target.files);
            e.target.value = '';
          }}
        />
        <button
          onClick={() => photoRef.current?.click()}
          className="px-3 py-2 border border-white/20 text-[var(--color-text)] rounded-lg hover:bg-white/10 transition-colors text-sm"
        >
          📷 Photo
        </button>
        <button
          onClick={() => videoRef.current?.click()}
          className="px-3 py-2 border border-white/20 text-[var(--color-text)] rounded-lg hover:bg-white/10 transition-colors text-sm"
        >
          🎥 Video
        </button>
        <button
          onClick={() => create.mutate()}
          disabled={!canSubmit}
          className="ml-auto px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 disabled:opacity-50 transition-colors"
        >
          {create.isPending ? 'Saving…' : 'Log it'}
        </button>
      </div>
    </div>
  );
}
