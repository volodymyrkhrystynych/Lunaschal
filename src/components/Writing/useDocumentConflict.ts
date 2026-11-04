import { useCallback, useEffect, useRef, useState } from 'react';
import {
  clearConflict,
  heldConflict,
  holdConflict,
  rememberRevision,
  resetRevision,
} from '../../lib/writingDocuments';

interface Document {
  title: string;
  content: string;
  revision?: number;
}

interface Options {
  documentKey: string;
  document: Document | undefined;
  /** Whether the text on screen has been saved, so a newer copy can replace it. */
  isClean: () => boolean;
  load: (document: Document, text: string) => void;
  fetchLatest: () => Promise<Document>;
  saveText: (text: string, baseRevision: number | undefined) => void;
}

/** Loads a document into an editor and handles it having changed elsewhere,
 * for ChapterEditor and NoteEditor alike. */
export function useDocumentConflict({
  documentKey,
  document,
  isClean,
  load,
  fetchLatest,
  saveText,
}: Options) {
  const [conflict, setConflict] = useState(false);
  const loaded = useRef<{ key: string; revision: number } | null>(null);

  useEffect(() => {
    if (!document) return;
    const revision = document.revision ?? 0;
    const previous = loaded.current;
    const first = previous?.key !== documentKey;
    const newerAndClean =
      !first && revision > (previous?.revision ?? 0) && isClean();
    if (!first && !newerAndClean) return;
    loaded.current = { key: documentKey, revision };
    rememberRevision(documentKey, document.revision);
    const held = heldConflict(documentKey);
    load(document, held ?? document.content);
    setConflict(held !== null);
    // Only a new document or revision may replace what is on screen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [document, documentKey]);

  const refused = useCallback(() => setConflict(true), []);

  const holdEdit = useCallback(
    (text: string) => holdConflict(documentKey, text),
    [documentKey]
  );

  const keepMine = useCallback(
    async (text: string) => {
      const latest = await fetchLatest();
      resetRevision(documentKey, latest.revision);
      clearConflict(documentKey);
      setConflict(false);
      saveText(text, latest.revision);
    },
    [documentKey, fetchLatest, saveText]
  );

  const loadLatest = useCallback(async () => {
    const latest = await fetchLatest();
    resetRevision(documentKey, latest.revision);
    clearConflict(documentKey);
    setConflict(false);
    load(latest, latest.content);
    // `load` is a fresh closure each render and only sets state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentKey, fetchLatest]);

  return { conflict, refused, holdEdit, keepMine, loadLatest };
}
