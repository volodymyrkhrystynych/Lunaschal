import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type WritingContextDocSummary, type WritingContextDoc } from '../../hooks/api';

type DocType = 'character' | 'outline' | 'worldbuilding' | 'note';

const DOC_TYPE_LABELS: Record<DocType, string> = {
  character: 'Character',
  outline: 'Outline',
  worldbuilding: 'World',
  note: 'Note',
};

interface CheckboxListProps {
  projectId: string;
  selectedIds: Set<string>;
  onToggle: (docId: string) => void;
  onEdit: (docId: string) => void;
  onAdd: () => void;
}

export function ContextDocCheckboxList({ projectId, selectedIds, onToggle, onEdit, onAdd }: CheckboxListProps) {
  const { data: docs } = useQuery({
    queryKey: ['writing', 'context-docs', projectId],
    queryFn: () => api.writing.listContextDocs(projectId),
    enabled: !!projectId,
  });

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Context Docs</span>
        <button
          onClick={onAdd}
          className="text-xs text-[var(--color-primary)] hover:underline"
        >
          + Add
        </button>
      </div>
      {docs?.map((doc: WritingContextDocSummary) => (
        <div key={doc.id} className="flex items-center gap-2 group">
          <input
            type="checkbox"
            id={`ctx-${doc.id}`}
            checked={selectedIds.has(doc.id)}
            onChange={() => onToggle(doc.id)}
            className="accent-[var(--color-primary)]"
          />
          <label htmlFor={`ctx-${doc.id}`} className="text-xs text-[var(--color-text)] flex-1 cursor-pointer truncate">
            <span className="text-[var(--color-text-muted)] mr-1">[{DOC_TYPE_LABELS[doc.docType as DocType] ?? doc.docType}]</span>
            {doc.title}
          </label>
          <button
            onClick={() => onEdit(doc.id)}
            className="opacity-0 group-hover:opacity-100 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-all"
            title="Edit"
          >
            ✎
          </button>
        </div>
      ))}
      {(!docs || docs.length === 0) && (
        <div className="text-xs text-[var(--color-text-muted)]">No context docs yet</div>
      )}
    </div>
  );
}

interface EditorProps {
  projectId: string;
  docId: string | null;
  onClose: () => void;
}

export function ContextDocEditor({ projectId, docId, onClose }: EditorProps) {
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [docType, setDocType] = useState<DocType>('note');
  const queryClient = useQueryClient();

  const { data: existing } = useQuery({
    queryKey: ['writing', 'context-doc', docId],
    queryFn: () => api.writing.getContextDoc(docId!),
    enabled: !!docId,
  });

  useEffect(() => {
    if (existing) {
      setTitle(existing.title);
      setContent(existing.content);
      setDocType(existing.docType as DocType);
    }
  }, [existing]);

  const createDoc = useMutation({
    mutationFn: () => api.writing.createContextDoc(projectId, { title: title.trim(), content, docType }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['writing', 'context-docs', projectId] });
      onClose();
    },
  });

  const updateDoc = useMutation({
    mutationFn: () => api.writing.updateContextDoc(docId!, { title: title.trim(), content, docType }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['writing', 'context-docs', projectId] });
      queryClient.invalidateQueries({ queryKey: ['writing', 'context-doc', docId] });
      onClose();
    },
  });

  const deleteDoc = useMutation({
    mutationFn: () => api.writing.deleteContextDoc(docId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['writing', 'context-docs', projectId] });
      onClose();
    },
  });

  const handleSave = () => {
    if (!title.trim()) return;
    if (docId) updateDoc.mutate();
    else createDoc.mutate();
  };

  const isPending = createDoc.isPending || updateDoc.isPending;

  return (
    <div className="flex flex-col gap-2 p-3 border border-white/10 rounded bg-[var(--color-surface)]">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-[var(--color-text)]">{docId ? 'Edit Context Doc' : 'New Context Doc'}</span>
        <button onClick={onClose} className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]">✕</button>
      </div>

      <input
        autoFocus
        value={title}
        onChange={e => setTitle(e.target.value)}
        placeholder="Title…"
        className="px-2 py-1 text-sm rounded bg-white/5 border border-white/20 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
      />

      <select
        value={docType}
        onChange={e => setDocType(e.target.value as DocType)}
        className="px-2 py-1 text-sm rounded bg-white/5 border border-white/20 text-[var(--color-text)] focus:outline-none focus:border-[var(--color-primary)]"
      >
        {(Object.entries(DOC_TYPE_LABELS) as [DocType, string][]).map(([val, label]) => (
          <option key={val} value={val}>{label}</option>
        ))}
      </select>

      <textarea
        value={content}
        onChange={e => setContent(e.target.value)}
        placeholder="Content…"
        rows={6}
        className="px-2 py-1 text-sm rounded bg-white/5 border border-white/20 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)] resize-none leading-relaxed"
      />

      <div className="flex gap-2">
        <button
          onClick={handleSave}
          disabled={!title.trim() || isPending}
          className="flex-1 py-1 text-sm rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-50 transition-colors"
        >
          {isPending ? 'Saving…' : 'Save'}
        </button>
        {docId && (
          <button
            onClick={() => { if (confirm('Delete this context doc?')) deleteDoc.mutate(); }}
            disabled={deleteDoc.isPending}
            className="py-1 px-2 text-sm rounded hover:bg-red-500/20 text-[var(--color-text-muted)] hover:text-red-400 transition-colors"
          >
            Delete
          </button>
        )}
      </div>
    </div>
  );
}

export type { WritingContextDoc };
