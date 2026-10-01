/**
 * Splits a Files-tab upload into requests the backend will accept.
 *
 * Werkzeug caps a multipart body at 1000 parts (Flask's `MAX_FORM_PARTS`) and
 * answers 413 past it. Each file travels as two parts — the file and its
 * `relative_path` — so a folder of 500 files was refused outright. The byte
 * bound keeps any one request well inside the client's upload timeout.
 */
export const MAX_FILES_PER_BATCH = 200;
export const MAX_BYTES_PER_BATCH = 256 * 1024 * 1024;

export function uploadBatches<T extends { size: number }>(
  files: T[],
  maxFiles = MAX_FILES_PER_BATCH,
  maxBytes = MAX_BYTES_PER_BATCH
): T[][] {
  const batches: T[][] = [];
  let current: T[] = [];
  let bytes = 0;
  for (const file of files) {
    if (
      current.length &&
      (current.length >= maxFiles || bytes + file.size > maxBytes)
    ) {
      batches.push(current);
      current = [];
      bytes = 0;
    }
    current.push(file);
    bytes += file.size;
  }
  if (current.length) batches.push(current);
  return batches;
}
