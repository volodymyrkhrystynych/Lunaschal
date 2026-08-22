import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';

export function STTStatusSection() {
  const { data, isLoading } = useQuery({
    queryKey: ['stt', 'health'],
    queryFn: api.stt.health,
    refetchInterval: 5000,
  });

  const Row = ({
    label,
    ready,
    detail,
  }: {
    label: string;
    ready: boolean;
    detail: string;
  }) => (
    <div className="flex items-center gap-3">
      <span
        className={`w-2 h-2 rounded-full flex-shrink-0 ${ready ? 'bg-green-400' : 'bg-red-400'}`}
      />
      <div>
        <span className="text-sm text-[var(--color-text)]">{label}</span>
        <span className="text-xs text-[var(--color-text-muted)] ml-2">
          {detail}
        </span>
      </div>
      <span
        className={`ml-auto text-xs font-medium ${ready ? 'text-green-400' : 'text-red-400'}`}
      >
        {ready ? 'ready' : 'unavailable'}
      </span>
    </div>
  );

  return (
    <>
      {isLoading ? (
        <p className="text-sm text-[var(--color-text-muted)]">Checking…</p>
      ) : data ? (
        <>
          <Row
            label="Speech-to-text"
            ready={data.stt_ready}
            detail={`${data.stt_backend} · ${data.stt_model}`}
          />
          <Row
            label="Text-to-speech"
            ready={data.tts_ready}
            detail={data.tts_backend}
          />
          {(!data.stt_ready || !data.tts_ready) && (
            <div className="mt-3 pt-3 border-t border-white/10 text-xs text-[var(--color-text-muted)] space-y-1">
              <p>
                To enable local models:{' '}
                <code>pip install faster-whisper kokoro-onnx</code> (requires
                GPU)
              </p>
              <p>
                To use OpenAI: set{' '}
                <code>
                  STT_BACKEND=openai TTS_BACKEND=openai OPENAI_API_KEY=sk-…
                </code>{' '}
                and restart
              </p>
            </div>
          )}
        </>
      ) : (
        <p className="text-sm text-red-400">Could not reach STT service</p>
      )}
    </>
  );
}
