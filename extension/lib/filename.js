/**
 * The name the resume file goes out under.
 *
 * Mirrors `backend/jobs/render.py`'s `download_filename`. It is duplicated
 * rather than fetched because the extension builds a `File` object locally and
 * the name has to exist before any request is made — but the two are tested
 * against the same cases so they cannot drift apart unnoticed.
 */

// Path separators, the characters Windows forbids in a filename, and the
// control characters — the last because the backend's copy of this puts the
// value in a Content-Disposition header, where a bare CR or LF is header
// injection. Keeping both sides identical is the point.
// eslint-disable-next-line no-control-regex
const STRIP = /[\\/:*?"<>|\x00-\x1f\x7f]/g;
const MAX_STEM = 80;

export function downloadFilename(fullName, ext) {
  const cleaned = String(fullName ?? '')
    .normalize('NFC')
    .replace(STRIP, ' ')
    .replace(/\s+/g, ' ')
    .replace(/^[\s.]+|[\s.]+$/g, '')
    .slice(0, MAX_STEM)
    .trim();
  const stem = cleaned ? `${cleaned} Resume` : 'Resume';
  return `${stem}.${ext}`;
}
