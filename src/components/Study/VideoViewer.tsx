import { useEffect, useRef } from 'react';

// How often playback reports where it is. `timeupdate` fires roughly four
// times a second; a write per event would be a write per 250ms for the length
// of a lecture, and losing the last few seconds of a position costs nothing.
const SAVE_INTERVAL_MS = 5000;

// Don't resume from the last half-second of a video — finishing one and
// reopening it should start it over, not park it back on the credits.
const RESUME_TAIL_SECONDS = 2;

interface Props {
  fileUrl: string;
  /** Seconds in, from wherever this video was last left. */
  initialTime?: number;
  onTimeChange?: (seconds: number) => void;
}

export function VideoViewer({ fileUrl, initialTime, onTimeChange }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const lastSavedRef = useRef(0);
  // Read inside the event handlers, which are attached once per file.
  const initialRef = useRef(initialTime);
  initialRef.current = initialTime;
  const onTimeChangeRef = useRef(onTimeChange);
  onTimeChangeRef.current = onTimeChange;

  // Seek on `loadedmetadata`, not on mount: before it, `duration` is NaN and
  // there are no seekable ranges, so setting `currentTime` is either ignored
  // or clamped to zero. Range requests are already answered by the file route
  // (`send_file(..., conditional=True)`), so the seek itself genuinely works.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const restore = () => {
      const wanted = initialRef.current;
      if (!wanted || wanted <= 0) return;
      const duration = video.duration;
      if (Number.isFinite(duration) && wanted >= duration - RESUME_TAIL_SECONDS)
        return;
      video.currentTime = wanted;
    };

    const report = (seconds: number) => {
      lastSavedRef.current = Date.now();
      onTimeChangeRef.current?.(seconds);
    };

    const onTimeUpdate = () => {
      if (Date.now() - lastSavedRef.current < SAVE_INTERVAL_MS) return;
      report(video.currentTime);
    };

    // Pausing and leaving are the two moments the exact position matters, so
    // they bypass the interval rather than waiting out the rest of it.
    const onSettled = () => report(video.currentTime);

    video.addEventListener('loadedmetadata', restore);
    video.addEventListener('timeupdate', onTimeUpdate);
    video.addEventListener('pause', onSettled);
    video.addEventListener('seeked', onSettled);
    if (video.readyState >= 1) restore();

    return () => {
      video.removeEventListener('loadedmetadata', restore);
      video.removeEventListener('timeupdate', onTimeUpdate);
      video.removeEventListener('pause', onSettled);
      video.removeEventListener('seeked', onSettled);
      // The unmount is the last chance to record where playback got to.
      if (video.currentTime > 0) onTimeChangeRef.current?.(video.currentTime);
    };
  }, [fileUrl]);

  return (
    <div className="flex-1 flex items-center justify-center bg-black overflow-hidden">
      <video
        ref={videoRef}
        src={fileUrl}
        controls
        className="max-h-full max-w-full"
      />
    </div>
  );
}
