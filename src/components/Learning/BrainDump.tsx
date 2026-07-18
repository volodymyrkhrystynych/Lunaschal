import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { useRecorder } from '../../hooks/useRecorder';
import { useShortcutScope } from '../../shortcuts/ShortcutProvider';
import { parseTagsInput } from '../../lib/tags';

interface Props {
  folderId: string | null;
  onGenerated: () => void;
}

export function BrainDump({ folderId, onGenerated }: Props) {
  const [text, setText] = useState('');
  const [tags, setTags] = useState('');
  const [manual, setManual] = useState(false);
  const [manualCard, setManualCard] = useState({ question: '', answer: '' });
  const queryClient = useQueryClient();

  const recorder = useRecorder((t) => setText((prev) => (prev ? `${prev}\n${t}` : t)));

  useShortcutScope(2, {
    record: () => {
      if (recorder.status === 'recording') recorder.stop();
      else if (recorder.status === 'idle') recorder.start();
    },
  });

  const generate = useMutation({
    mutationFn: () => api.learning.generate({
      text,
      folderId: folderId ?? undefined,
      tags: parseTagsInput(tags),
    }),
    onSuccess: () => {
      setText('');
      queryClient.invalidateQueries({ queryKey: ['learning'] });
      onGenerated();
    },
  });

  const createManual = useMutation({
    mutationFn: () => api.learning.createCard({
      question: manualCard.question,
      answer: manualCard.answer,
      folderId: folderId ?? undefined,
      tags: parseTagsInput(tags),
    }),
    onSuccess: () => {
      setManualCard({ question: '', answer: '' });
      queryClient.invalidateQueries({ queryKey: ['learning'] });
    },
  });

  return (
    <div className="max-w-xl mx-auto space-y-4">
      <div className="bg-[var(--color-surface)] rounded-lg border border-white/10 p-6">
        <div className="mb-2 flex items-center justify-between">
          <label className="text-sm text-[var(--color-text-muted)]">
            Brain-dump — talk or type through what you're learning; the AI turns it into atomic cards for the approval queue
          </label>
          <button
            onClick={() => (recorder.status === 'recording' ? recorder.stop() : recorder.start())}
            disabled={recorder.status === 'transcribing'}
            className={`px-3 py-1 rounded text-sm font-medium transition-colors disabled:opacity-50 ${
              recorder.status === 'recording'
                ? 'bg-red-600 hover:bg-red-700 text-white'
                : 'bg-white/10 hover:bg-white/20 text-[var(--color-text)]'
            }`}>
            {recorder.status === 'recording' ? '■ Stop' : recorder.status === 'transcribing' ? 'Transcribing…' : '🎤 Record'}
          </button>
        </div>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Paste a transcript, type notes, or hit Record and talk it through…"
          rows={8}
          className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg p-3 resize-y focus:outline-none focus:border-[var(--color-primary)]"
        />
        {recorder.error && <p className="text-xs text-red-400 mt-1">{recorder.error}</p>}
        <div className="mt-3 mb-4">
          <input
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder="Tags (comma-separated, optional)"
            className="w-full bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg p-2.5 focus:outline-none focus:border-[var(--color-primary)]"
          />
        </div>
        <button
          onClick={() => generate.mutate()}
          disabled={!text.trim() || generate.isPending}
          className="w-full py-3 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors font-medium disabled:opacity-50">
          {generate.isPending ? 'Generating cards…' : 'Generate Cards'}
        </button>
        {generate.isError && (
          <p className="text-xs text-red-400 mt-2">
            {generate.error instanceof Error ? generate.error.message : 'Generation failed'}
          </p>
        )}
      </div>

      <div className="bg-[var(--color-surface)] rounded-lg border border-white/10">
        <button
          onClick={() => setManual((m) => !m)}
          className="w-full px-6 py-3 text-left text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors">
          {manual ? '▾' : '▸'} Write a single card manually (skips the queue)
        </button>
        {manual && (
          <div className="px-6 pb-6 space-y-3">
            <textarea
              value={manualCard.question}
              onChange={(e) => setManualCard({ ...manualCard, question: e.target.value })}
              placeholder="Question" rows={2}
              className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg p-3 resize-none focus:outline-none focus:border-[var(--color-primary)]"
            />
            <textarea
              value={manualCard.answer}
              onChange={(e) => setManualCard({ ...manualCard, answer: e.target.value })}
              placeholder="Answer" rows={2}
              className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg p-3 resize-none focus:outline-none focus:border-[var(--color-primary)]"
            />
            <button
              onClick={() => createManual.mutate()}
              disabled={!manualCard.question.trim() || !manualCard.answer.trim() || createManual.isPending}
              className="w-full py-2.5 bg-white/10 text-[var(--color-text)] rounded-lg hover:bg-white/20 transition-colors disabled:opacity-50">
              Create Card
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
