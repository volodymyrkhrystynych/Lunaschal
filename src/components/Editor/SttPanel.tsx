import { useRef, useState, useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { useRecorder } from '../../hooks/useRecorder';

interface Props {
  onTranscribed: (text: string) => void;
  onMeetingUploaded: (id: string) => void;
}

type Status = 'idle' | 'recording' | 'transcribing';
type CorrectStatus = 'idle' | 'working';

interface CorrectResult {
  raw: string;
  corrected: string;
}

export function SttPanel({ onTranscribed, onMeetingUploaded }: Props) {
  const [lastText, setLastText] = useState('');
  const [error, setError] = useState('');
  const clearTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const recordModeRef = useRef<'normal' | 'journal'>('normal');
  const queryClient = useQueryClient();

  const saveJournalFromVoice = useMutation({
    mutationFn: (text: string) => api.journal.createFromVoice(text),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['journal'] }),
    onError: err =>
      setError(
        err instanceof Error ? err.message : 'Failed to save journal entry'
      ),
  });

  const recorder = useRecorder(text => {
    setLastText(text);
    if (recordModeRef.current === 'journal') saveJournalFromVoice.mutate(text);
    else onTranscribed(text);
    if (clearTimerRef.current) clearTimeout(clearTimerRef.current);
    clearTimerRef.current = setTimeout(() => setLastText(''), 8000);
  });
  const status = recorder.status;

  const [expanded, setExpanded] = useState(false);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [groundTruth, setGroundTruth] = useState('');
  const [correctStatus, setCorrectStatus] = useState<CorrectStatus>('idle');
  const [correctResult, setCorrectResult] = useState<CorrectResult | null>(
    null
  );
  const [correctError, setCorrectError] = useState('');
  const audioInputRef = useRef<HTMLInputElement | null>(null);

  const { data: listenerState } = useQuery({
    queryKey: ['stt', 'listener-state'],
    queryFn: api.stt.listenerState,
    refetchInterval: 500,
  });

  const { data: activeMeeting } = useQuery({
    queryKey: ['meetings', 'active'],
    queryFn: api.meetings.active,
    refetchInterval: 1000,
  });
  const meetingActive = !!activeMeeting?.id;
  const [meetingElapsed, setMeetingElapsed] = useState('');

  useEffect(() => {
    if (!meetingActive || !activeMeeting?.startedAt) {
      setMeetingElapsed('');
      return;
    }
    const startedMs = new Date(activeMeeting.startedAt).getTime();
    const tick = () => {
      const s = Math.max(0, Math.floor((Date.now() - startedMs) / 1000));
      setMeetingElapsed(
        `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
      );
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [meetingActive, activeMeeting?.startedAt]);

  const invalidateMeetings = () => {
    queryClient.invalidateQueries({ queryKey: ['meetings'] });
    queryClient.invalidateQueries({ queryKey: ['meetings', 'active'] });
  };

  const startMeeting = useMutation({
    mutationFn: api.meetings.start,
    onSuccess: invalidateMeetings,
    onError: err =>
      setError(err instanceof Error ? err.message : 'Failed to start meeting'),
  });

  const stopMeeting = useMutation({
    mutationFn: (id: string) => api.meetings.stop(id),
    onSuccess: invalidateMeetings,
    onError: err =>
      setError(err instanceof Error ? err.message : 'Failed to stop meeting'),
  });

  const meetingPending = startMeeting.isPending || stopMeeting.isPending;

  const uploadMeeting = useMutation({
    mutationFn: () => api.meetings.upload(audioFile!),
    onSuccess: data => {
      setAudioFile(null);
      setCorrectResult(null);
      if (audioInputRef.current) audioInputRef.current.value = '';
      invalidateMeetings();
      onMeetingUploaded(data.id);
    },
    onError: err =>
      setCorrectError(
        err instanceof Error ? err.message : 'Failed to upload meeting'
      ),
  });

  useEffect(
    () => () => {
      if (clearTimerRef.current) clearTimeout(clearTimerRef.current);
    },
    []
  );

  const startRecording = () => {
    setError('');
    recordModeRef.current = 'normal';
    void recorder.start();
  };
  const startJournalRecording = () => {
    setError('');
    recordModeRef.current = 'journal';
    void recorder.start();
  };
  const stopRecording = recorder.stop;

  const handleCorrect = async () => {
    if (!audioFile) return;
    setCorrectStatus('working');
    setCorrectError('');
    setCorrectResult(null);
    try {
      const fd = new FormData();
      fd.append('audio', audioFile);
      if (groundTruth.trim()) fd.append('ground_truth', groundTruth.trim());
      const r = await fetch('/api/transcribe/correct', {
        method: 'POST',
        body: fd,
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || 'Failed');
      setCorrectResult({ raw: data.raw, corrected: data.corrected });
    } catch (err) {
      setCorrectError(err instanceof Error ? err.message : 'Failed');
    } finally {
      setCorrectStatus('idle');
    }
  };

  const listenerRecording = listenerState?.recording ?? false;
  const listenerTranscribing = listenerState?.transcribing ?? false;
  const listenerMode = listenerState?.mode ?? null;
  const isListenerActive = listenerRecording || listenerTranscribing;
  const effectiveStatus: Status =
    status !== 'idle'
      ? status
      : listenerRecording
        ? 'recording'
        : listenerTranscribing
          ? 'transcribing'
          : 'idle';

  const isListenerControlling = isListenerActive && status === 'idle';
  const isJournalMode = isListenerControlling && listenerMode === 'journal';

  // The in-app Journal button shares the same recorder/mic as the Record
  // button (only one recording can run at a time) — recordModeRef tracks
  // which one is "holding" it so each button reflects its own state.
  const inAppJournalActive =
    status !== 'idle' && recordModeRef.current === 'journal';
  const inAppNormalActive =
    status !== 'idle' && recordModeRef.current === 'normal';

  const buttonDisabled =
    effectiveStatus === 'transcribing' ||
    isListenerControlling ||
    inAppJournalActive;

  const buttonLabel = inAppJournalActive
    ? 'Record'
    : effectiveStatus === 'recording'
      ? isJournalMode
        ? 'Journal…'
        : isListenerControlling
          ? 'Recording…'
          : 'Stop'
      : effectiveStatus === 'transcribing'
        ? isJournalMode
          ? 'Saving journal…'
          : 'Transcribing…'
        : 'Record';

  const journalButtonDisabled =
    inAppNormalActive ||
    isListenerControlling ||
    saveJournalFromVoice.isPending ||
    (status === 'transcribing' && !inAppJournalActive);

  const journalButtonLabel = inAppJournalActive
    ? status === 'recording'
      ? 'Stop'
      : 'Transcribing…'
    : saveJournalFromVoice.isPending
      ? 'Saving…'
      : 'Journal';

  return (
    <div className="shrink-0 border-t border-white/10 bg-[var(--color-surface)]">
      {expanded && (
        <div className="p-4 border-b border-white/10 flex flex-col gap-3">
          <div className="flex gap-3 items-start">
            <div className="flex flex-col gap-1.5 flex-1 min-w-0">
              <label className="text-xs text-[var(--color-text-muted)]">
                Audio file
              </label>
              <div className="flex gap-2 items-center">
                <button
                  onClick={() => audioInputRef.current?.click()}
                  className="px-3 py-1 rounded text-xs bg-white/10 hover:bg-white/20 text-[var(--color-text)] transition-colors"
                >
                  {audioFile ? audioFile.name : 'Choose file…'}
                </button>
                {audioFile && (
                  <button
                    onClick={() => {
                      setAudioFile(null);
                      setCorrectResult(null);
                      if (audioInputRef.current)
                        audioInputRef.current.value = '';
                    }}
                    className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                  >
                    ✕
                  </button>
                )}
              </div>
              <input
                ref={audioInputRef}
                type="file"
                accept="audio/*"
                className="hidden"
                onChange={e => {
                  setAudioFile(e.target.files?.[0] ?? null);
                  setCorrectResult(null);
                  setCorrectError('');
                }}
              />
            </div>
            <button
              onClick={handleCorrect}
              disabled={!audioFile || correctStatus === 'working'}
              className="mt-5 px-3 py-1 rounded text-sm font-medium bg-white/10 hover:bg-white/20 text-[var(--color-text)] transition-colors disabled:opacity-40"
            >
              {correctStatus === 'working'
                ? 'Working…'
                : 'Transcribe & Correct'}
            </button>
            <button
              onClick={() => uploadMeeting.mutate()}
              disabled={!audioFile || uploadMeeting.isPending}
              title="Process this file as a meeting recording — transcribed and diarized like a live meeting, in the Meetings tab"
              className="mt-5 px-3 py-1 rounded text-sm font-medium bg-white/10 hover:bg-white/20 text-[var(--color-text)] transition-colors disabled:opacity-40"
            >
              {uploadMeeting.isPending ? 'Uploading…' : 'Add as meeting'}
            </button>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs text-[var(--color-text-muted)]">
              Ground truth reference (paste document text)
            </label>
            <textarea
              value={groundTruth}
              onChange={e => setGroundTruth(e.target.value)}
              placeholder="Paste reference text here — names, terms, domain vocabulary the LLM will use to correct the transcription…"
              rows={4}
              className="w-full rounded bg-white/5 border border-white/10 px-3 py-2 text-xs text-[var(--color-text)] placeholder-[var(--color-text-muted)] resize-y focus:outline-none focus:border-white/25"
            />
          </div>

          {correctError && (
            <p className="text-xs text-red-400">{correctError}</p>
          )}

          {correctResult && (
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-1.5">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-[var(--color-text-muted)]">
                    Raw transcription
                  </span>
                  <button
                    onClick={() => onTranscribed(correctResult.raw)}
                    className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
                  >
                    Use raw ↑
                  </button>
                </div>
                <pre className="text-xs text-[var(--color-text-muted)] bg-white/5 rounded p-2 whitespace-pre-wrap break-words">
                  {correctResult.raw}
                </pre>
              </div>
              <div className="flex flex-col gap-1.5">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-[var(--color-text-muted)]">
                    Corrected
                  </span>
                  <button
                    onClick={() => onTranscribed(correctResult.corrected)}
                    className="text-xs font-medium text-[var(--color-text)] hover:opacity-80 transition-opacity"
                  >
                    Use corrected ↑
                  </button>
                </div>
                <pre className="text-xs text-[var(--color-text)] bg-white/5 rounded p-2 whitespace-pre-wrap break-words border border-white/10">
                  {correctResult.corrected}
                </pre>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="h-10 flex items-center gap-2 md:gap-3 px-2 md:px-4 overflow-x-auto">
        <button
          onClick={
            effectiveStatus === 'recording' && !isListenerControlling
              ? stopRecording
              : startRecording
          }
          disabled={buttonDisabled}
          className={`shrink-0 flex items-center gap-1.5 px-3 py-1 rounded text-sm font-medium transition-colors disabled:opacity-50 ${
            effectiveStatus === 'recording' && isJournalMode
              ? 'bg-amber-600 hover:bg-amber-700 text-white'
              : effectiveStatus === 'recording'
                ? 'bg-red-600 hover:bg-red-700 text-white'
                : 'bg-white/10 hover:bg-white/20 text-[var(--color-text)]'
          }`}
        >
          <span
            className={`w-2 h-2 rounded-full ${
              effectiveStatus === 'recording' && isJournalMode
                ? 'bg-white animate-pulse'
                : effectiveStatus === 'recording'
                  ? 'bg-white animate-pulse'
                  : effectiveStatus === 'transcribing'
                    ? 'bg-yellow-400'
                    : 'bg-[var(--color-text-muted)]'
            }`}
          />
          {buttonLabel}
        </button>

        <button
          onClick={
            inAppJournalActive && status === 'recording'
              ? stopRecording
              : startJournalRecording
          }
          disabled={journalButtonDisabled}
          title="Record → transcribe → save as a journal entry (same as the journal voice shortcut)"
          className={`shrink-0 flex items-center gap-1.5 px-3 py-1 rounded text-sm font-medium transition-colors disabled:opacity-50 ${
            inAppJournalActive && status === 'recording'
              ? 'bg-amber-600 hover:bg-amber-700 text-white'
              : 'bg-white/10 hover:bg-white/20 text-[var(--color-text)]'
          }`}
        >
          <span
            className={`w-2 h-2 rounded-full ${
              inAppJournalActive && status === 'recording'
                ? 'bg-white animate-pulse'
                : inAppJournalActive && status === 'transcribing'
                  ? 'bg-yellow-400'
                  : 'bg-[var(--color-text-muted)]'
            }`}
          />
          {journalButtonLabel}
        </button>

        {(error || recorder.error) && (
          <span className="text-xs text-red-400 truncate">
            {error || recorder.error}
          </span>
        )}
        {!error && !recorder.error && lastText && (
          <span className="hidden md:inline text-xs text-[var(--color-text-muted)] truncate">
            "{lastText}"
          </span>
        )}
        {!error &&
          !recorder.error &&
          !lastText &&
          effectiveStatus === 'idle' && (
            <span className="hidden md:inline text-xs text-[var(--color-text-muted)]">
              Voice input — transcribes into active editor or clipboard
            </span>
          )}

        <button
          onClick={() => {
            setError('');
            if (meetingActive && activeMeeting?.id)
              stopMeeting.mutate(activeMeeting.id);
            else startMeeting.mutate();
          }}
          disabled={meetingPending}
          title={
            meetingActive
              ? 'Stop the meeting recording and start transcription'
              : 'Record a meeting (mic + system audio)'
          }
          className={`ml-auto shrink-0 flex items-center gap-1.5 px-3 py-1 rounded text-sm font-medium transition-colors disabled:opacity-50 ${
            meetingActive
              ? 'bg-red-600 hover:bg-red-700 text-white'
              : 'bg-white/10 hover:bg-white/20 text-[var(--color-text)]'
          }`}
        >
          <span
            className={`w-2 h-2 rounded-full ${
              meetingActive
                ? 'bg-white animate-pulse'
                : 'bg-[var(--color-text-muted)]'
            }`}
          />
          {meetingActive ? `Stop meeting ${meetingElapsed}` : 'Meeting'}
        </button>

        <button
          onClick={() => {
            setExpanded(e => !e);
            setCorrectResult(null);
            setCorrectError('');
          }}
          className={`shrink-0 flex items-center gap-1 px-2 py-0.5 rounded text-xs transition-colors ${
            expanded
              ? 'bg-white/15 text-[var(--color-text)]'
              : 'bg-white/5 hover:bg-white/10 text-[var(--color-text-muted)]'
          }`}
        >
          File
          <span
            className={`transition-transform ${expanded ? 'rotate-180' : ''}`}
          >
            ▲
          </span>
        </button>
      </div>
    </div>
  );
}
