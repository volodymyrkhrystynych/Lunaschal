import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { KeyRecorder } from './KeyRecorder';

export function ShortcutsSection() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });
  const [pasteKey, setPasteKey] = useState<string | null>(null);
  const [voiceKey, setVoiceKey] = useState<string | null>(null);
  const [journalKey, setJournalKey] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings) {
      setPasteKey(settings.sttPasteKey ?? null);
      setVoiceKey(settings.sttVoiceKey ?? null);
      setJournalKey(settings.sttJournalKey ?? null);
    }
  }, [settings]);

  const save = useMutation({
    mutationFn: () =>
      api.settings.updateShortcuts({
        sttPasteKey: pasteKey ?? undefined,
        sttVoiceKey: voiceKey ?? undefined,
        sttJournalKey: journalKey ?? undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    },
  });

  const togglePipeline = useMutation({
    mutationFn: (enabled: boolean) =>
      api.settings.updateAI({ voicePipelineEnabled: enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const togglePolish = useMutation({
    mutationFn: (enabled: boolean) =>
      api.settings.updateAI({ transcribePolishEnabled: enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const pipelineEnabled = settings?.voicePipelineEnabled ?? true;
  const polishEnabled = settings?.transcribePolishEnabled ?? true;

  return (
    <>
      <p className="text-sm text-[var(--color-text-muted)]">
        Click a shortcut button then press the key you want. Restart the STT
        listener for changes to take effect.
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <p className="text-sm text-[var(--color-text)] mb-1.5">
            Paste shortcut
          </p>
          <p className="text-xs text-[var(--color-text-muted)] mb-2">
            Record → transcribe → paste at cursor
          </p>
          <KeyRecorder value={pasteKey} onChange={setPasteKey} />
          <p className="text-xs text-[var(--color-text-muted)] mt-1">
            env: <code>STT_PASTE_KEY</code>
          </p>
        </div>
        <div>
          <p className="text-sm text-[var(--color-text)] mb-1.5">
            Voice shortcut
          </p>
          <p className="text-xs text-[var(--color-text-muted)] mb-2">
            {pipelineEnabled
              ? 'Record → transcribe → AI chat → TTS reply'
              : 'Record → transcribe → paste at cursor'}
          </p>
          <KeyRecorder value={voiceKey} onChange={setVoiceKey} />
          <p className="text-xs text-[var(--color-text-muted)] mt-1">
            env: <code>STT_VOICE_KEY</code>
          </p>
        </div>
        <div>
          <p className="text-sm text-[var(--color-text)] mb-1.5">
            Journal shortcut
          </p>
          <p className="text-xs text-[var(--color-text-muted)] mb-2">
            Record → transcribe → save as journal entry
          </p>
          <KeyRecorder value={journalKey} onChange={setJournalKey} />
          <p className="text-xs text-[var(--color-text-muted)] mt-1">
            env: <code>STT_JOURNAL_KEY</code>
          </p>
        </div>
      </div>

      <label className="flex items-center gap-3 cursor-pointer select-none pt-1">
        <div
          onClick={() => togglePipeline.mutate(!pipelineEnabled)}
          className={`relative w-9 h-5 rounded-full transition-colors ${pipelineEnabled ? 'bg-[var(--color-primary)]' : 'bg-white/20'}`}
        >
          <span
            className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${pipelineEnabled ? 'translate-x-4' : 'translate-x-0'}`}
          />
        </div>
        <span className="text-sm text-[var(--color-text)]">
          AI chat + TTS reply on voice shortcut
        </span>
        {!pipelineEnabled && (
          <span className="text-xs text-[var(--color-text-muted)]">
            (voice shortcut pastes instead)
          </span>
        )}
      </label>

      <label className="flex items-center gap-3 cursor-pointer select-none pt-1">
        <div
          onClick={() => togglePolish.mutate(!polishEnabled)}
          className={`relative w-9 h-5 rounded-full transition-colors ${polishEnabled ? 'bg-[var(--color-primary)]' : 'bg-white/20'}`}
        >
          <span
            className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${polishEnabled ? 'translate-x-4' : 'translate-x-0'}`}
          />
        </div>
        <span className="text-sm text-[var(--color-text)]">
          Polish transcriptions with AI
        </span>
        <span className="text-xs text-[var(--color-text-muted)]">
          (fixes punctuation, capitalisation, obvious mishearings)
        </span>
      </label>

      <div className="flex items-center gap-3 pt-1">
        <button
          onClick={() => save.mutate()}
          disabled={save.isPending}
          className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50 text-sm"
        >
          {save.isPending ? 'Saving…' : 'Save shortcuts'}
        </button>
        {saved && <span className="text-sm text-green-400">Saved</span>}
      </div>
    </>
  );
}
