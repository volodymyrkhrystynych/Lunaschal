import { api } from '../../hooks/api';
import {
  offlineReason,
  viewerKindFor,
  type StudySource,
} from '../../lib/study';
import { PdfViewer } from './PdfViewer';
import { VideoViewer } from './VideoViewer';

interface Props {
  source: StudySource;
  /**
   * Where the reader has got to — a page number for a PDF, seconds for a
   * video. Called often, so the desk's handler must be cheap.
   */
  onPosition?: (position: number) => void;
}

/** The left half of the desk: whatever this source is, rendered. */
export function SourceViewer({ source, onPosition }: Props) {
  const kind = viewerKindFor(source);
  const fileUrl = api.study.fileUrl(source.id);

  if (kind === 'importing') {
    const phase = source.importProgress?.phase;
    return (
      <Centered>
        <div className="animate-pulse">Importing…</div>
        {phase && phase !== 'done' && (
          <div className="mt-1 text-xs opacity-70">{phase}</div>
        )}
      </Centered>
    );
  }

  if (kind === 'error') {
    return (
      <Centered>
        <div className="text-[var(--color-text)]">This import failed.</div>
        {source.importError && (
          <div className="mt-2 text-xs max-w-md">{source.importError}</div>
        )}
      </Centered>
    );
  }

  // Videos live on the external archive drive and nowhere else — there is no
  // second copy by design, so an unplugged drive means no playback. Saying so
  // beats a <video> element sitting silently on a 404.
  if (kind === 'offline') {
    return (
      <Centered>
        <div className="text-[var(--color-text)]">{offlineReason(source)}</div>
        <div className="mt-2 text-xs max-w-md">
          Downloaded videos are kept on the archive drive rather than backed up.
          Plug it in to watch this one.
        </div>
      </Centered>
    );
  }

  // `position` is one column whose meaning the row's `kind` decides: a page
  // number here, seconds below.
  if (kind === 'pdf') {
    return (
      <PdfViewer
        fileUrl={fileUrl}
        initialPage={source.position ?? undefined}
        onPageChange={onPosition}
      />
    );
  }

  if (kind === 'video') {
    return (
      <VideoViewer
        fileUrl={fileUrl}
        initialTime={source.position ?? undefined}
        onTimeChange={onPosition}
      />
    );
  }

  // An archived page — and the one kind that stores no position. The file is
  // rendered inside `sandbox=""`, which puts the frame in an opaque origin: its
  // scroll offset is unreadable and unsettable from out here, by design. Every
  // way of reaching in weakens the second layer this frame exists to be, for a
  // scroll offset.
  //
  // It was sanitized with nh3 before it was ever written to
  // disk, and it is *still* served into a sandboxed iframe: it is third-party
  // markup on our own origin, so one layer of "this is safe" is not a layer.
  // `allow-same-origin` is deliberately absent, which is what puts the frame in
  // an opaque origin with no access to our cookies or storage.
  return (
    <iframe
      src={fileUrl}
      sandbox=""
      title={source.title || 'Archived page'}
      className="flex-1 w-full bg-white border-0"
    />
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center p-6 text-center text-sm text-[var(--color-text-muted)]">
      {children}
    </div>
  );
}
