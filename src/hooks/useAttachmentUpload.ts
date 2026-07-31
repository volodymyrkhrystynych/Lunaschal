import { useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from './api';
import { defaultNameFor } from '../lib/journalAttachments';

/**
 * Uploads files as attachments of one journal entry.
 *
 * Not a `useMutation`: a paste can carry several files, and they have to go up
 * one at a time so the server's `position` counter orders them the way they were
 * pasted. The hook owns the sequencing and a single user-visible error instead.
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
          await api.journal.attachments.upload(
            entryId,
            file,
            defaultNameFor(file.name)
          );
        }
      } catch (e) {
        setError((e as Error).message || 'Upload failed');
      } finally {
        setIsUploading(false);
        queryClient.invalidateQueries({ queryKey: ['journal'] });
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
