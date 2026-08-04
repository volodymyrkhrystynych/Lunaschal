import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type IdeaSketch } from '../../hooks/api';
import { SketchPicker } from './SketchPicker';

interface SketchStripProps {
  ideaId: string;
}

/**
 * Paper pages borrowed into an idea, rendered from the page's PNG snapshot —
 * no copying, no new storage (the JournalPaperItem pattern).
 */
export function SketchStrip({ ideaId }: SketchStripProps) {
  const queryClient = useQueryClient();
  const [picking, setPicking] = useState(false);
  const [preview, setPreview] = useState<IdeaSketch | null>(null);

  const { data: sketches } = useQuery({
    queryKey: ['ideas', ideaId, 'sketches'],
    queryFn: () => api.ideas.listSketches(ideaId),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['ideas', ideaId, 'sketches'] });
    queryClient.invalidateQueries({ queryKey: ['ideas'] });
  };

  const caption = useMutation({
    mutationFn: ({ id, text }: { id: string; text: string }) =>
      api.ideas.updateSketch(id, { caption: text }),
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.ideas.removeSketch(id),
    onSuccess: invalidate,
  });

  return (
    <div className="mx-4 my-4">
      <div className="flex items-center gap-2 mb-2">
        <h3 className="text-sm font-medium text-[var(--color-text)]">
          Sketches
        </h3>
        <button
          type="button"
          onClick={() => setPicking(true)}
          className="px-2 py-0.5 rounded text-xs bg-white/10 text-[var(--color-text)] hover:bg-white/15"
        >
          + Add from Paper
        </button>
      </div>

      {/* Vision is off in this project — both llama presets skip the vision
          tower — so the agent reads the note, not the drawing. Say so, rather
          than shipping a button that silently does nothing. */}
      <p className="text-xs text-[var(--color-text-muted)] mb-2">
        Add a note describing each sketch — the agent reads the note, not the
        drawing.
      </p>

      {!sketches || sketches.length === 0 ? (
        <p className="text-xs text-[var(--color-text-muted)]">
          No sketches yet.
        </p>
      ) : (
        <ul className="flex gap-3 overflow-x-auto pb-2">
          {sketches.map(sketch => (
            <li key={sketch.id} className="w-40 shrink-0">
              <button
                type="button"
                onClick={() => setPreview(sketch)}
                className="block w-full aspect-[210/297] bg-white rounded overflow-hidden border border-white/10 hover:border-[var(--color-primary)]"
              >
                {sketch.imageUrl && (
                  <img
                    src={sketch.imageUrl}
                    alt={sketch.caption || 'Paper sketch'}
                    className="w-full h-full object-cover object-top"
                  />
                )}
              </button>
              <input
                defaultValue={sketch.caption}
                onBlur={e => {
                  if (e.target.value !== sketch.caption) {
                    caption.mutate({ id: sketch.id, text: e.target.value });
                  }
                }}
                placeholder="Describe this sketch…"
                aria-label="Sketch description"
                className="w-full mt-1 bg-transparent text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border-b border-white/10 focus:outline-none focus:border-[var(--color-primary)]"
              />
              <button
                type="button"
                onClick={() => remove.mutate(sketch.id)}
                className="mt-1 text-xs text-[var(--color-text-muted)] hover:text-red-400"
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      {picking && (
        <SketchPicker
          ideaId={ideaId}
          onClose={() => setPicking(false)}
          onAdded={invalidate}
        />
      )}

      {preview?.imageUrl && (
        <div
          role="dialog"
          aria-label="Sketch preview"
          onClick={() => setPreview(null)}
          className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4"
        >
          <img
            src={preview.imageUrl}
            alt={preview.caption || 'Paper sketch'}
            className="max-h-full max-w-full object-contain bg-white rounded"
          />
        </div>
      )}
    </div>
  );
}
