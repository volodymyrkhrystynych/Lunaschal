import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type FoodMedia } from '../../hooks/api';

export function FoodDescriptions({ media }: { media: FoodMedia[] }) {
  const queryClient = useQueryClient();
  const describe = useMutation({
    mutationFn: api.food.describeMedia,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['food'] }),
  });
  const photos = media.filter(m => m.kind === 'image');
  if (!photos.length) return null;
  return (
    <div className="mb-3 space-y-2 text-sm text-[var(--color-text-muted)]">
      {photos.map((m, i) => (
        <div key={m.id}>
          {m.description && (
            <details>
              <summary className="cursor-pointer">
                Photo {i + 1} description
              </summary>
              <p className="whitespace-pre-wrap mt-1">{m.description}</p>
            </details>
          )}
          {m.descriptionStatus === 'running' ? (
            <span role="status">Describing photo {i + 1}…</span>
          ) : (
            <button
              onClick={() => describe.mutate(m.id)}
              disabled={describe.isPending}
              className="text-xs underline disabled:opacity-50"
            >
              {m.description ? 'Describe again' : `Describe photo ${i + 1}`}
            </button>
          )}
          {m.descriptionStatus === 'error' && (
            <p role="alert">
              {m.descriptionError || 'Photo description failed'}
            </p>
          )}
        </div>
      ))}
      {describe.error && <p role="alert">{describe.error.message}</p>}
    </div>
  );
}
