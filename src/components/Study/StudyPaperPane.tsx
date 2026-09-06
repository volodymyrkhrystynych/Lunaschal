import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { ulid } from '../../lib/ulid';
import { usePaperCreate } from '../../offline/mutationDefaults';
import type { StudySource } from '../../lib/study';
import { PaperEditor, type PaperEditorHandle } from '../Paper/PaperEditor';

interface Props {
  source: StudySource;
  /** Set by the desk so it can commit the open page before unmounting this. */
  handleRef?: React.RefObject<PaperEditorHandle | null>;
}

/**
 * The other right half of the desk: a handwriting paper instead of a note.
 *
 * Study borrows a *whole* paper rather than modelling pages again — the brief
 * asked for "the opportunity to create new pages", which is what a paper is —
 * so `paper_id` is an ordinary `papers` row and the same document is reachable
 * from the Paper tab, its explorer grid and the Journal like any other. That is
 * the same borrowing `idea_sketches` does for a single page.
 *
 * The paper is made lazily, on the first switch into this mode, exactly as the
 * Notebook file is: a source you only skimmed in the other mode leaves no empty
 * paper behind.
 */
export function StudyPaperPane({ source, handleRef }: Props) {
  const queryClient = useQueryClient();
  // Both ids are minted here so the paper exists on the device immediately and
  // the create replays when the backend is next in reach — the same shape
  // `Paper` uses for the ＋ button. The *binding* is an ordinary PATCH, like
  // StudyNotePane's, and it waits for the create (see below).
  const create = usePaperCreate();
  const [createdId, setCreatedId] = useState<string | null>(null);
  // Guards against React 18's double-invoked effects and against a re-render
  // landing before the PATCH resolves — StudyNotePane's `creatingRef`.
  const creatingRef = useRef(false);
  const [error, setError] = useState('');

  const bindPaper = useMutation({
    mutationFn: (paperId: string) => api.study.update(source.id, { paperId }),
    onSuccess: updated => {
      queryClient.setQueryData(['study', 'source', source.id], updated);
      queryClient.invalidateQueries({ queryKey: ['study', 'sources'] });
    },
  });

  useEffect(() => {
    setCreatedId(null);
  }, [source.id]);

  useEffect(() => {
    if (source.paperId || creatingRef.current) return;
    creatingRef.current = true;
    const paperId = ulid();
    void (async () => {
      try {
        // Shown immediately, before either request: the paper exists on this
        // device the moment its id is minted, and waiting on the round trip
        // would leave a blank pane on bad wifi.
        setCreatedId(paperId);
        // Awaited, and this order matters. `paper_id` is a real foreign key,
        // so a PATCH that overtakes the POST is answered "No such paper" and
        // the binding is simply lost — the pane would keep working for this
        // session and come back to the text editor on the next one. Offline
        // the create *pauses* rather than failing (its promise never settles),
        // which is the behaviour that wants waiting on: the binding lands when
        // the server does.
        await create.mutateAsync({ id: paperId, pageId: ulid() });
        await bindPaper.mutateAsync(paperId);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Could not start a paper');
      } finally {
        creatingRef.current = false;
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.id, source.paperId]);

  const paperId = source.paperId ?? createdId;

  if (!paperId) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-2 p-6 text-center text-sm text-[var(--color-text-muted)]">
        <span>{error || 'Starting a paper…'}</span>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {error && (
        <div className="shrink-0 px-3 py-1 text-xs bg-amber-500/20 border-b border-amber-500/40 text-[var(--color-text)]">
          {error} — the paper is on this device; it will bind when the server is
          back.
        </div>
      )}
      <PaperEditor paperId={paperId} embedded ref={handleRef} />
    </div>
  );
}
