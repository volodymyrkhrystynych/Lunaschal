import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import { addDays, todayISO } from '@/lib/lifestyle';

/** Days of history in the thumbnail strip — enough that a gap is obvious. */
const STRIP_DAYS = 14;

export function SelfieCard() {
  const queryClient = useQueryClient();
  const videoRef = useRef<HTMLVideoElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [live, setLive] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: selfies = [] } = useQuery({
    queryKey: ['lifestyle', 'selfies'],
    queryFn: () => api.lifestyle.selfies.list(120),
  });

  const stopCamera = () => {
    streamRef.current?.getTracks().forEach(t => t.stop());
    streamRef.current = null;
    setLive(false);
  };

  // Never leave the camera light on when the tab moves away.
  useEffect(() => stopCamera, []);

  const upload = useMutation({
    mutationFn: (image: Blob) => api.lifestyle.selfies.upload(image),
    onSuccess: () => {
      setError(null);
      stopCamera();
      queryClient.invalidateQueries({ queryKey: ['lifestyle', 'selfies'] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.lifestyle.selfies.delete(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['lifestyle', 'selfies'] }),
  });

  const startCamera = async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user' },
      });
      streamRef.current = stream;
      setLive(true);
      // The <video> only exists once `live` is true, so attach on the next paint.
      requestAnimationFrame(() => {
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          void videoRef.current.play().catch(() => {});
        }
      });
    } catch {
      // The Pocket 2's camera support is an open question in the doc, and a
      // desktop without a webcam lands here too — the file picker below is
      // always available, so this is a downgrade, not a dead end.
      setError('No camera available — use "Choose photo" instead.');
      setLive(false);
    }
  };

  const capture = () => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return;
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d')?.drawImage(video, 0, 0);
    canvas.toBlob(
      blob => {
        if (blob) upload.mutate(blob);
      },
      'image/jpeg',
      0.9
    );
  };

  // A fixed run of recent days, so a day with no selfie shows as an empty slot
  // rather than silently collapsing out of the strip.
  const today = todayISO();
  const byDate = new Map(selfies.map(s => [s.date, s]));
  const strip = Array.from({ length: STRIP_DAYS }, (_, i) => {
    const date = addDays(today, -(STRIP_DAYS - 1 - i));
    return { date, selfie: byDate.get(date) ?? null };
  });
  const todaySelfie = byDate.get(today) ?? null;

  return (
    <section className="p-4 rounded-lg bg-[var(--color-surface)] border border-white/10 flex flex-col min-w-0">
      <div className="flex items-baseline justify-between gap-2 mb-3">
        <h2 className="text-lg font-semibold text-[var(--color-text)]">
          Daily selfie
        </h2>
        {todaySelfie && (
          <span className="text-xs text-[var(--color-text-muted)]">
            Logged today
          </span>
        )}
      </div>

      {live ? (
        <div className="flex flex-col gap-2">
          <video
            ref={videoRef}
            playsInline
            muted
            className="w-full max-h-64 rounded bg-black object-cover"
          />
          <div className="flex gap-2">
            <button
              type="button"
              onClick={capture}
              disabled={upload.isPending}
              className="flex-1 px-3 py-2 rounded bg-[var(--color-primary)] text-white text-sm disabled:opacity-40 min-h-[44px]"
            >
              {upload.isPending ? 'Saving…' : 'Capture'}
            </button>
            <button
              type="button"
              onClick={stopCamera}
              className="px-3 py-2 rounded border border-white/10 text-[var(--color-text)] text-sm min-h-[44px]"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void startCamera()}
            className="flex-1 px-3 py-2 rounded bg-[var(--color-primary)] text-white text-sm min-h-[44px]"
          >
            {todaySelfie ? 'Retake' : 'Take selfie'}
          </button>
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="px-3 py-2 rounded border border-white/10 text-[var(--color-text)] text-sm min-h-[44px]"
          >
            Choose photo
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            capture="user"
            className="hidden"
            onChange={e => {
              const file = e.target.files?.[0];
              if (file) upload.mutate(file);
              e.target.value = '';
            }}
          />
        </div>
      )}

      {error && (
        <div className="mt-2 text-xs text-[var(--color-accent)]">{error}</div>
      )}

      <div className="mt-3 flex gap-1 overflow-x-auto pb-1">
        {strip.map(({ date, selfie }) => (
          <div key={date} className="shrink-0 text-center">
            {selfie ? (
              <button
                type="button"
                onClick={() => remove.mutate(selfie.id)}
                title={`${date} — click to delete`}
                className="block w-12 h-12 rounded overflow-hidden border border-white/10 hover:border-[var(--color-primary)]"
              >
                <img
                  src={selfie.url}
                  alt={`Selfie from ${date}`}
                  className="w-full h-full object-cover"
                />
              </button>
            ) : (
              <div
                title={`${date} — no selfie`}
                className="w-12 h-12 rounded border border-dashed border-white/10 bg-[var(--color-bg)]"
              />
            )}
            <div className="mt-0.5 text-[10px] text-[var(--color-text-muted)]">
              {date.slice(8)}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
