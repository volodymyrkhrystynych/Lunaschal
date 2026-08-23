export type PreviewKind =
  'text' | 'image' | 'video' | 'audio' | 'pdf' | 'other';

// Extensions EditorPane's CodeMirror setup already opens as text (see
// EditorPane.tsx's getLang), plus the plain-text extensions that never got a
// language mode but still belong in the editor rather than the drive preview.
const TEXT_EXTENSIONS = new Set([
  'md',
  'markdown',
  'js',
  'jsx',
  'ts',
  'tsx',
  'py',
  'txt',
  'json',
  'yml',
  'yaml',
  'toml',
  'ini',
  'cfg',
  'sh',
  'css',
  'html',
  'csv',
  'log',
]);

const IMAGE_EXTENSIONS = new Set([
  'jpg',
  'jpeg',
  'png',
  'gif',
  'webp',
  'svg',
  'bmp',
  'avif',
]);

const VIDEO_EXTENSIONS = new Set(['mp4', 'webm', 'mov', 'mkv', 'ogv']);

const AUDIO_EXTENSIONS = new Set(['mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac']);

/** What kind of viewer a file's extension should open in — the Editor's
 * detail pane routes on this to pick CodeMirror vs. an inline media preview
 * vs. a plain download card. Extension-based, not content-sniffed: the file
 * may not have been fetched yet when this needs to decide.
 *
 * No extension at all defaults to 'text' rather than 'other': that's a file
 * the "+ New" button just created (or a README/.gitignore-style name), and
 * CodeMirror opens it fine — a fresh file must stay editable, not immediately
 * show as an undownloadable-until-saved card. An extension that IS present
 * but unrecognized defaults to 'other' instead, since that's far more likely
 * to be a real binary format (an uploaded file) than a stray text file. */
export function previewKind(path: string): PreviewKind {
  const slash = path.lastIndexOf('/');
  const name = slash < 0 ? path : path.slice(slash + 1);
  const dot = name.lastIndexOf('.');
  // dot <= 0 covers both no extension and a dotfile like .gitignore, whose
  // leading dot isn't a real extension separator.
  if (dot <= 0) return 'text';
  const ext = name.slice(dot + 1).toLowerCase();
  if (!ext) return 'text';

  if (TEXT_EXTENSIONS.has(ext)) return 'text';
  if (IMAGE_EXTENSIONS.has(ext)) return 'image';
  if (VIDEO_EXTENSIONS.has(ext)) return 'video';
  if (AUDIO_EXTENSIONS.has(ext)) return 'audio';
  if (ext === 'pdf') return 'pdf';
  return 'other';
}
