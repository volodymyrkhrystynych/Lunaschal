import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../hooks/api';
import type { Meeting, MeetingPhase } from '../hooks/api';
import { useShortcuts, useShortcutScope } from '../shortcuts/ShortcutProvider';

const PHASE_LABELS: Partial<Record<MeetingPhase, string>> = {
  recording: 'Recording…',
  transcribing_mic: 'Transcribing (your mic)…',
  transcribing_system: 'Transcribing (participants)…',
  diarizing: 'Identifying speakers…',
  summarizing: 'Summarizing…',
};

// 'Me' always gets the primary color; other speakers cycle a stable palette.
const SPEAKER_COLORS = ['text-sky-400', 'text-emerald-400', 'text-amber-400', 'text-fuchsia-400', 'text-rose-400', 'text-teal-400'];

function speakerColor(speaker: string): string {
  if (speaker === 'Me') return 'text-[var(--color-primary)]';
  const n = parseInt(speaker.replace(/\D/g, ''), 10);
  return SPEAKER_COLORS[(Number.isNaN(n) ? 0 : n - 1 + SPEAKER_COLORS.length) % SPEAKER_COLORS.length];
}

const formatDate = (date: string) => new Intl.DateTimeFormat('en-US', {
  weekday: 'short', year: 'numeric', month: 'short', day: 'numeric',
  hour: '2-digit', minute: '2-digit',
}).format(new Date(date));

function formatDuration(seconds: number | null): string {
  if (seconds == null) return '';
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m ${s % 60}s`;
}

const meetingTitle = (m: { title: string | null; startedAt: string }) =>
  m.title || `Meeting ${formatDate(m.startedAt)}`;

const isBusy = (m: Meeting) => m.status === 'recording' || m.status === 'transcribing';

export function Meetings() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selIndex, setSelIndex] = useState(0);
  const queryClient = useQueryClient();
  const { level } = useShortcuts();

  const { data: meetings, isLoading } = useQuery({
    queryKey: ['meetings'],
    queryFn: api.meetings.list,
    refetchInterval: (q) => (q.state.data?.some(isBusy) ? 1500 : false),
  });

  useEffect(() => {
    setSelIndex((i) => Math.min(i, Math.max((meetings?.length ?? 1) - 1, 0)));
  }, [meetings]);

  useShortcutScope(1, {
    next: () => setSelIndex((i) => Math.min(i + 1, Math.max((meetings?.length ?? 1) - 1, 0))),
    prev: () => setSelIndex((i) => Math.max(i - 1, 0)),
    drillIn: () => {
      const m = meetings?.[selIndex];
      if (!m) return false;
      setSelectedId(m.id);
      return true;
    },
    drillOut: () => {
      if (!selectedId) return false;
      setSelectedId(null);
      return true;
    },
  });

  if (selectedId) {
    return <MeetingDetailView id={selectedId} onBack={() => setSelectedId(null)} />;
  }

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold text-[var(--color-text)]">Meetings</h1>
      </div>

      <div className="flex-1 overflow-y-auto space-y-3">
        {isLoading && <div className="text-[var(--color-text-muted)]">Loading...</div>}

        {meetings?.map((m, idx) => (
          <button key={m.id}
            onClick={() => setSelectedId(m.id)}
            ref={(el) => { if (el && level >= 1 && idx === selIndex) el.scrollIntoView({ block: 'nearest' }); }}
            className={`w-full text-left p-4 bg-[var(--color-surface)] rounded-lg border transition-colors hover:border-white/30 ${
              level >= 1 && idx === selIndex ? 'border-[var(--color-primary)]' : 'border-white/10'
            }`}>
            <div className="flex items-center justify-between gap-3">
              <span className="font-medium text-[var(--color-text)] truncate">{meetingTitle(m)}</span>
              <StatusPill meeting={m} />
            </div>
            <div className="flex items-center gap-3 mt-1 text-sm text-[var(--color-text-muted)]">
              <span>{formatDate(m.startedAt)}</span>
              {m.durationSeconds != null && <span>{formatDuration(m.durationSeconds)}</span>}
              {m.hasSummary && <span title="Has AI summary">📝</span>}
              {m.hasNotes && <span title="Has notes">🗒️</span>}
            </div>
            {m.status === 'transcribing' && (
              <div className="mt-2">
                <div className="text-xs text-[var(--color-text-muted)] mb-1">{PHASE_LABELS[m.phase] ?? 'Processing…'}</div>
                <div className="h-1.5 bg-white/10 rounded overflow-hidden">
                  <div className="h-full w-1/3 bg-[var(--color-primary)] rounded animate-pulse" />
                </div>
              </div>
            )}
            {m.status === 'error' && m.error && (
              <div className="mt-2 text-sm text-red-400">{m.error}</div>
            )}
          </button>
        ))}

        {meetings?.length === 0 && !isLoading && (
          <div className="text-center text-[var(--color-text-muted)] py-12">
            No meetings yet. Use the Meeting button in the bottom bar to record one, or the File panel to add one from an audio file.
          </div>
        )}
      </div>
    </div>
  );
}

function StatusPill({ meeting }: { meeting: Meeting }) {
  const styles: Record<Meeting['status'], string> = {
    recording: 'border-red-400/50 text-red-400 bg-red-500/10',
    transcribing: 'border-yellow-400/50 text-yellow-400 bg-yellow-500/10',
    done: 'border-green-400/40 text-green-400 bg-green-500/10',
    error: 'border-red-400/50 text-red-400 bg-red-500/10',
  };
  const labels: Record<Meeting['status'], string> = {
    recording: '● Recording',
    transcribing: 'Processing',
    done: 'Done',
    error: 'Error',
  };
  return (
    <span className={`shrink-0 px-2 py-0.5 text-xs rounded-full border ${styles[meeting.status]}`}>
      {labels[meeting.status]}
    </span>
  );
}

function MeetingDetailView({ id, onBack }: { id: string; onBack: () => void }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState<string | null>(null);
  const [notes, setNotes] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<{ label: string; value: string } | null>(null);
  // Escape unmounts the rename input, which can still fire onBlur — this flag
  // stops that blur from saving the discarded value.
  const renameCancelledRef = useRef(false);

  const { data: meeting, isLoading } = useQuery({
    queryKey: ['meetings', id],
    queryFn: () => api.meetings.get(id),
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === 'recording' || s === 'transcribing' ? 1500 : false;
    },
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['meetings'] });
    queryClient.invalidateQueries({ queryKey: ['meetings', id] });
  };

  const update = useMutation({
    mutationFn: (data: { title?: string; notes?: string; speakerNames?: Record<string, string> | null }) =>
      api.meetings.update(id, data),
    onSuccess: invalidate,
  });

  const summarize = useMutation({
    mutationFn: () => api.meetings.summarize(id),
    onSuccess: invalidate,
  });

  const deleteMeeting = useMutation({
    mutationFn: () => api.meetings.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['meetings'] });
      onBack();
    },
  });

  if (isLoading || !meeting) {
    return (
      <div className="flex-1 flex flex-col p-4">
        <div className="text-[var(--color-text-muted)]">{isLoading ? 'Loading...' : 'Meeting not found'}</div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      <div className="flex items-center gap-3 mb-4">
        <button onClick={onBack} className="px-3 py-1.5 rounded border border-white/20 text-[var(--color-text-muted)] hover:bg-white/10">
          ← Back
        </button>
        <input
          value={title ?? meeting.title ?? ''}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() => { if (title !== null && title !== (meeting.title ?? '')) update.mutate({ title }); }}
          placeholder={`Meeting ${formatDate(meeting.startedAt)}`}
          className="flex-1 bg-transparent text-xl font-semibold text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-b focus:border-[var(--color-primary)]"
        />
        <button onClick={() => { if (confirm('Delete this meeting and its audio?')) deleteMeeting.mutate(); }}
          disabled={deleteMeeting.isPending || meeting.status === 'recording'}
          className="px-3 py-1.5 rounded border border-red-400/50 text-red-400 hover:bg-red-500/10 disabled:opacity-50">
          Delete
        </button>
      </div>

      <div className="text-sm text-[var(--color-text-muted)] mb-4 flex items-center gap-3">
        <span>{formatDate(meeting.startedAt)}</span>
        {meeting.durationSeconds != null && <span>{formatDuration(meeting.durationSeconds)}</span>}
      </div>

      <div className="flex-1 overflow-y-auto space-y-4 pr-1">
        {(meeting.status === 'recording' || meeting.status === 'transcribing') && (
          <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
            <div className="text-sm text-[var(--color-text-muted)] mb-2">
              {PHASE_LABELS[meeting.phase] ?? 'Processing…'}
            </div>
            <div className="h-1.5 bg-white/10 rounded overflow-hidden">
              <div className="h-full w-1/3 bg-[var(--color-primary)] rounded animate-pulse" />
            </div>
            {meeting.status === 'transcribing' && (
              <div className="text-xs text-[var(--color-text-muted)] mt-2">
                Transcription runs the largest Whisper model on CPU — this can take a while.
              </div>
            )}
          </div>
        )}

        {meeting.status === 'error' && (
          <div className="p-4 rounded-lg border border-red-400/40 bg-red-500/10 text-sm text-red-400">
            {meeting.error || 'Something went wrong processing this meeting.'}
          </div>
        )}

        {meeting.status !== 'recording' && (
          <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10 space-y-3">
            <h3 className="text-sm font-medium text-[var(--color-text)]">Audio</h3>
            {meeting.source === 'upload' ? (
              <audio controls preload="none" src={api.meetings.audioUrl(id, 'system')} className="w-full h-9" />
            ) : (
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-3">
                  <span className="text-xs text-[var(--color-text-muted)] w-24 shrink-0">My mic</span>
                  <audio controls preload="none" src={api.meetings.audioUrl(id, 'mic')} className="w-full h-9" />
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-[var(--color-text-muted)] w-24 shrink-0">Participants</span>
                  <audio controls preload="none" src={api.meetings.audioUrl(id, 'system')} className="w-full h-9" />
                </div>
              </div>
            )}
          </div>
        )}

        {meeting.status === 'done' && (
          <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-medium text-[var(--color-text)]">Summary</h3>
              <button onClick={() => summarize.mutate()} disabled={summarize.isPending}
                className="text-sm text-[var(--color-primary)] hover:text-[var(--color-primary)]/80 disabled:opacity-50">
                {summarize.isPending ? 'Summarizing…' : meeting.summary ? 'Regenerate' : 'Generate summary'}
              </button>
            </div>
            {summarize.isError && (
              <div className="text-sm text-red-400 mb-2">{(summarize.error as Error).message}</div>
            )}
            {meeting.summary ? (
              <div className="text-sm text-[var(--color-text)] whitespace-pre-wrap">{meeting.summary}</div>
            ) : (
              <div className="text-sm text-[var(--color-text-muted)] italic">No summary yet.</div>
            )}
          </div>
        )}

        {meeting.segments && meeting.segments.length > 0 && (() => {
          const names = meeting.speakerNames ?? {};
          const displayName = (label: string) => names[label] || label;
          const speakers = [...new Set(meeting.segments!.map((s) => s.speaker))];
          const saveRename = () => {
            if (renameCancelledRef.current) {
              renameCancelledRef.current = false;
              return;
            }
            if (!renaming) return;
            const trimmed = renaming.value.trim();
            const next = { ...names };
            // An empty name (or the canonical label itself) reverts the rename.
            if (!trimmed || trimmed === renaming.label) delete next[renaming.label];
            else next[renaming.label] = trimmed;
            setRenaming(null);
            if (JSON.stringify(next) !== JSON.stringify(names)) {
              update.mutate({ speakerNames: next });
            }
          };
          return (
          <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-medium text-[var(--color-text)]">Transcript</h3>
              <span className="text-xs text-[var(--color-text-muted)]">Click a speaker to rename</span>
            </div>
            <div className="flex flex-wrap gap-1.5 mb-3">
              {speakers.map((label) =>
                renaming?.label === label ? (
                  <input key={label} autoFocus value={renaming.value}
                    onChange={(e) => setRenaming({ label, value: e.target.value })}
                    onBlur={saveRename}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') e.currentTarget.blur();
                      if (e.key === 'Escape') { renameCancelledRef.current = true; setRenaming(null); }
                    }}
                    placeholder={label}
                    className="px-2 py-0.5 text-xs rounded-full border border-[var(--color-primary)] bg-transparent text-[var(--color-text)] w-32 focus:outline-none" />
                ) : (
                  <button key={label}
                    onClick={() => { renameCancelledRef.current = false; setRenaming({ label, value: names[label] ?? '' }); }}
                    title={names[label] ? `Rename ${label} (currently "${names[label]}")` : `Rename ${label}`}
                    className={`px-2 py-0.5 text-xs rounded-full border border-white/20 bg-white/5 hover:border-white/40 transition-colors ${speakerColor(label)}`}>
                    {displayName(label)}{names[label] && <span className="opacity-50 ml-1">✎</span>}
                  </button>
                )
              )}
            </div>
            <div className="space-y-2">
              {meeting.segments!.map((seg, i) => (
                <div key={i} className="flex gap-3 text-sm">
                  <span className="text-xs text-[var(--color-text-muted)] pt-0.5 shrink-0 tabular-nums">
                    {`${String(Math.floor(seg.start / 60)).padStart(2, '0')}:${String(Math.floor(seg.start % 60)).padStart(2, '0')}`}
                  </span>
                  <div className="min-w-0">
                    <span className={`font-medium ${speakerColor(seg.speaker)}`}>{displayName(seg.speaker)}: </span>
                    <span className="text-[var(--color-text)]">{seg.text}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
          );
        })()}

        {meeting.status === 'done' && (!meeting.segments || meeting.segments.length === 0) && (
          <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10 text-sm text-[var(--color-text-muted)] italic">
            No speech was detected in this recording.
          </div>
        )}

        <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
          <h3 className="text-sm font-medium text-[var(--color-text)] mb-2">Notes</h3>
          <textarea
            value={notes ?? meeting.notes}
            onChange={(e) => setNotes(e.target.value)}
            onBlur={() => { if (notes !== null && notes !== meeting.notes) update.mutate({ notes }); }}
            placeholder="Your notes about this meeting…"
            rows={4}
            className="w-full bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-y focus:outline-none"
          />
        </div>
      </div>
    </div>
  );
}
