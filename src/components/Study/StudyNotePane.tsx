import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { noteSlugFor, type StudySource } from '../../lib/study';
import { NotebookEditorPane } from '../Notebook/NotebookEditorPane';

interface Props {
  source: StudySource;
}

/**
 * The right half of the desk.
 *
 * Notes are ordinary Notebook files rather than a store of their own, so the
 * editor, its vim bindings, `[[wiki links]]`, `:find` and the offline-aware
 * debounced save are the ones already built — and a study note is reachable
 * from the Notebook tab like any other note.
 */
export function StudyNotePane({ source }: Props) {
  const queryClient = useQueryClient();
  // The path the pane is *showing*, which a wiki link can move off the bound
  // note without rebinding the source. `:q` brings it back.
  const [openPath, setOpenPath] = useState<string | null>(source.notePath);
  const [history, setHistory] = useState<string[]>([]);
  const [rebinding, setRebinding] = useState(false);
  // Guards the create-on-first-open below against React 18's double-invoked
  // effects and against a re-render landing before the PATCH resolves.
  const creatingRef = useRef(false);

  const bindNote = useMutation({
    mutationFn: (notePath: string) => api.study.update(source.id, { notePath }),
    onSuccess: updated => {
      queryClient.setQueryData(['study', 'source', source.id], updated);
      queryClient.invalidateQueries({ queryKey: ['study', 'sources'] });
    },
  });

  useEffect(() => {
    setOpenPath(source.notePath);
    setHistory([]);
  }, [source.id, source.notePath]);

  // A source with no note gets one the first time it is opened, named after
  // its title. Doing it here rather than at import time means a source you
  // only ever skimmed doesn't leave an empty note behind in the notebook.
  useEffect(() => {
    if (source.notePath || creatingRef.current) return;
    creatingRef.current = true;
    const path = noteSlugFor(source.title, source.id);
    void (async () => {
      try {
        await api.notebook.files.ensure(path);
        await bindNote.mutateAsync(path);
        setOpenPath(path);
      } finally {
        creatingRef.current = false;
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.id, source.notePath]);

  const openPathAndRemember = (path: string) => {
    setHistory(h => (openPath ? [...h, openPath] : h));
    setOpenPath(path);
  };

  const goBack = () => {
    setHistory(h => {
      if (h.length === 0) return h;
      setOpenPath(h[h.length - 1]);
      return h.slice(0, -1);
    });
  };

  if (!openPath) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-[var(--color-text-muted)]">
        Opening a note…
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <NotebookEditorPane
        key={source.id}
        filePath={openPath}
        onOpenPath={openPathAndRemember}
        onGoBack={goBack}
        homePath={source.notePath ?? openPath}
        homeLabel="this source's note"
        autoFocus={false}
      />
      <div className="shrink-0 flex items-center gap-2 px-3 py-1 border-t border-white/10 bg-[var(--color-surface)] text-xs text-[var(--color-text-muted)]">
        {rebinding ? (
          <RebindForm
            initial={source.notePath ?? ''}
            onCancel={() => setRebinding(false)}
            onSubmit={async path => {
              await api.notebook.files.ensure(path);
              await bindNote.mutateAsync(path);
              setOpenPath(path);
              setHistory([]);
              setRebinding(false);
            }}
          />
        ) : (
          <>
            <span className="truncate">{source.notePath ?? openPath}</span>
            <span className="flex-1" />
            <button
              type="button"
              onClick={() => setRebinding(true)}
              className="px-2 py-0.5 rounded hover:bg-white/10"
            >
              Change note
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function RebindForm({
  initial,
  onSubmit,
  onCancel,
}: {
  initial: string;
  onSubmit: (path: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    const path = value.trim();
    if (!path || busy) return;
    setBusy(true);
    try {
      await onSubmit(path);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <input
        autoFocus
        value={value}
        onChange={e => setValue(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter') void submit();
          if (e.key === 'Escape') onCancel();
        }}
        placeholder="study/my-note.md"
        aria-label="Note path"
        className="flex-1 px-2 py-0.5 rounded bg-[var(--color-bg)] border border-white/10 text-[var(--color-text)] outline-none"
      />
      <button
        type="button"
        onClick={() => void submit()}
        disabled={busy}
        className="px-2 py-0.5 rounded hover:bg-white/10 disabled:opacity-50"
      >
        Bind
      </button>
      <button
        type="button"
        onClick={onCancel}
        className="px-2 py-0.5 rounded hover:bg-white/10"
      >
        Cancel
      </button>
    </>
  );
}
