import { useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ulid } from '../lib/ulid';
import { storePhoto } from '../offline/photoStore';
import { enqueueJournalAttachment } from '../offline/photoQueue';
import { defaultNameFor } from '../lib/journalAttachments';

/**
 * Attaches files to one journal entry that already exists.
 *
 * Not a `useMutation`: a paste can carry several files, and each is written to
 * the device and queued in turn so the server's `position` counter orders them
 * the way they were pasted. The hook owns the sequencing and a single
 * user-visible error instead.
 *
 * The upload itself is the durable queue's job (`enqueueJournalAttachment`), not
 * this hook's. It used to POST each file directly, which meant a photo added on
 * a bad connection was lost the moment the request failed — the same bug the
 * composer had, in a second copy. `isUploading` therefore covers writing the
 * bytes to the device, which is the part the user has to wait for; what happens
 * after that is allowed to take until the phone next has signal.
 */
export function useAttachmentUpload(entryId: string) {
  const queryClient = useQueryClient();
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const uploadFiles = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return;
      setIsUploading(true);
      setError(null);
      try {
        for (const file of files) {
          const attachmentId = ulid();
          await storePhoto(attachmentId, file, 'journal', entryId);
          void enqueueJournalAttachment(
            queryClient,
            attachmentId,
            entryId,
            defaultNameFor(file.name)
          );
        }
      } catch (e) {
        // What is left to fail here is reading the file off the device — a
        // picker reference that stopped resolving, or an iCloud photo whose
        // full-size original never came down. A failed *upload* is no longer an
        // error the user has to see: it is queued, and it retries.
        setError((e as Error).message || 'Upload failed');
      } finally {
        setIsUploading(false);
      }
    },
    [entryId, queryClient]
  );

  return {
    uploadFiles,
    isUploading,
    error,
    setError,
  };
}
