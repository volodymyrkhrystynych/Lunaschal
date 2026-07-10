import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../hooks/api';
import { shiftDateISO, isFutureDate } from '../lib/newspapers';

function formatDateISO(date: Date) {
  return date.toISOString().split('T')[0];
}

function formatDisplayDate(date: string) {
  return new Date(`${date}T00:00:00Z`).toLocaleDateString('en-US', {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric', timeZone: 'UTC',
  });
}

function Lightbox({ src, alt, onClose }: { src: string; alt: string; onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <img src={src} alt={alt} className="max-w-full max-h-full rounded-lg shadow-2xl" onClick={(e) => e.stopPropagation()} />
      <button onClick={onClose} className="absolute top-4 right-4 text-white/80 hover:text-white text-2xl">✕</button>
    </div>
  );
}

export function Newspapers() {
  const today = formatDateISO(new Date());
  const [selectedDate, setSelectedDate] = useState(today);
  const [lightbox, setLightbox] = useState<{ src: string; alt: string } | null>(null);
  const queryClient = useQueryClient();

  const { data: pages, isLoading } = useQuery({
    queryKey: ['newspapers', selectedDate],
    queryFn: () => api.newspapers.getByDate(selectedDate),
  });

  const sync = useMutation({
    mutationFn: api.newspapers.sync,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['newspapers', today] }),
  });

  // Fires once when the tab is opened, and only for today — this is the
  // "daily download" trigger. Browsing to a past date never re-syncs.
  useEffect(() => {
    if (selectedDate === today) sync.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold text-[var(--color-text)]">Newspapers</h1>
        {sync.isPending && <span className="text-sm text-[var(--color-text-muted)]">Checking for today's front pages…</span>}
      </div>

      <div className="flex items-center justify-center gap-3 mb-4">
        <button onClick={() => setSelectedDate((d) => shiftDateISO(d, -1))}
          className="p-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)]">←</button>
        <h2 className="text-lg font-medium text-[var(--color-text)] w-72 text-center">{formatDisplayDate(selectedDate)}</h2>
        <button onClick={() => setSelectedDate((d) => shiftDateISO(d, 1))} disabled={isFutureDate(shiftDateISO(selectedDate, 1))}
          className="p-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-30 disabled:cursor-not-allowed">→</button>
        <input type="date" value={selectedDate} max={today} onChange={(e) => e.target.value && setSelectedDate(e.target.value)}
          className="ml-2 bg-white/5 border border-white/10 rounded px-2 py-1 text-sm text-[var(--color-text)]" />
      </div>

      {isLoading ? (
        <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">Loading…</div>
      ) : (
        <div className="flex-1 grid grid-cols-1 md:grid-cols-2 gap-4 overflow-y-auto">
          {pages?.map((page) => (
            <div key={page.paper} className="flex flex-col items-center">
              <h3 className="font-medium text-[var(--color-text)] mb-2">{page.label}</h3>
              {page.imageUrl ? (
                <img src={page.imageUrl} alt={`${page.label} front page, ${page.date}`}
                  onClick={() => setLightbox({ src: page.imageUrl!, alt: `${page.label} front page, ${page.date}` })}
                  className="w-full max-w-md rounded-lg border border-white/10 cursor-zoom-in hover:opacity-90 transition-opacity" />
              ) : (
                <div className="w-full max-w-md aspect-[3/4] rounded-lg border border-white/10 bg-white/5 flex items-center justify-center text-center text-sm text-[var(--color-text-muted)] p-4">
                  No front page saved for this date
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {lightbox && <Lightbox src={lightbox.src} alt={lightbox.alt} onClose={() => setLightbox(null)} />}
    </div>
  );
}
