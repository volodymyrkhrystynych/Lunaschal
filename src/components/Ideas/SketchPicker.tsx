import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';

interface SketchPickerProps {
  ideaId: string;
  onClose: () => void;
  onAdded: () => void;
}

/**
 * A flat grid of every Paper page that has a snapshot. Flat rather than grouped
 * by paper because picking is "find the drawing", not "browse the notebook" —
 * the paper title is a caption on the tile.
 */
export function SketchPicker({ ideaId, onClose, onAdded }: SketchPickerProps) {
  const { data: pages, isLoading } = useQuery({
    queryKey: ['ideas', 'paper-pages'],
    queryFn: api.ideas.paperPages,
  });

  const add = useMutation({
    mutationFn: (pageId: string) => api.ideas.addSketch(ideaId, { pageId }),
    onSuccess: () => {
      onAdded();
      onClose();
    },
  });

  return (
    <div
      role="dialog"
      aria-label="Pick a Paper page"
      className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        onClick={e => e.stopPropagation()}
        className="bg-[var(--color-surface)] rounded-lg border border-white/10 max-w-3xl w-full max-h-[80vh] flex flex-col overflow-hidden"
      >
        <div className="flex items-center gap-2 px-4 py-3 border-b border-white/10">
          <h2 className="text-sm font-medium text-[var(--color-text)]">
            Add a sketch from Paper
          </h2>
          <span className="flex-1" />
          <button
            type="button"
            onClick={onClose}
            className="px-2 py-0.5 rounded text-xs text-[var(--color-text-muted)] hover:bg-white/10"
          >
            Close
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {isLoading ? (
            <p className="text-sm text-[var(--color-text-muted)]">
              Loading pages…
            </p>
          ) : !pages || pages.length === 0 ? (
            <p className="text-sm text-[var(--color-text-muted)]">
              No saved Paper pages yet — draw one in the Paper tab first.
            </p>
          ) : (
            <ul className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {pages.map(page => (
                <li key={page.pageId}>
                  <button
                    type="button"
                    disabled={add.isPending}
                    onClick={() => add.mutate(page.pageId)}
                    className="block w-full text-left disabled:opacity-50"
                  >
                    <span className="block aspect-[210/297] bg-white rounded overflow-hidden border border-white/10 hover:border-[var(--color-primary)]">
                      {page.imageUrl && (
                        <img
                          src={page.imageUrl}
                          alt=""
                          className="w-full h-full object-cover object-top"
                        />
                      )}
                    </span>
                    <span className="block mt-1 text-xs text-[var(--color-text-muted)] truncate">
                      {page.paperTitle || 'Untitled'} · p{page.position + 1}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
