import { api } from '../../hooks/api';
import { viewerKindFor, type StudySource } from '../../lib/study';
import { PdfViewer } from './PdfViewer';

interface Props {
  source: StudySource;
}

/** The left half of the desk: whatever this source is, rendered. */
export function SourceViewer({ source }: Props) {
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

  if (kind === 'pdf') return <PdfViewer fileUrl={fileUrl} />;

  if (kind === 'video') {
    return (
      <div className="flex-1 flex items-center justify-center bg-black overflow-hidden">
        {/* Seeking works because the file route answers Range requests
            (send_file(..., conditional=True)). */}
        <video src={fileUrl} controls className="max-h-full max-w-full" />
      </div>
    );
  }

  // An archived page. It was sanitized with nh3 before it was ever written to
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
