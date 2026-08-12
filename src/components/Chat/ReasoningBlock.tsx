interface ReasoningBlockProps {
  content: string;
  /** True while the reply is still streaming, which changes only the summary. */
  live?: boolean;
}

/**
 * The model's reasoning channel, collapsed and muted.
 *
 * Only rendered when llama.cpp thinking is on (Settings → llama.cpp) — with it
 * off there is no reasoning content and this never appears. Collapsed by
 * default because reasoning is not the answer: it is long, it is written to
 * itself rather than to the user, and Gemma 4 emits far more of it than reply.
 *
 * Rendered from `metadata.thinking` (backend/delegate/runs.py persists it) as
 * well as from the live stream, so a reload shows the same trace — and so a
 * reply that came out empty still has an account of where the turn went. It is
 * shown, never sent: nothing feeds this text back to the model.
 *
 * Normally nested inside AgentSteps rather than used directly; it stands alone
 * only when a turn produced reasoning and no tool steps.
 *
 * Kept as plain pre-wrapped text rather than markdown. Reasoning is riddled with
 * half-finished lists and stray `#`/`*` that a markdown renderer turns into
 * headings and italics — formatting the model never meant, applied to text it
 * never meant anyone to read.
 */
export function ReasoningBlock({ content, live = false }: ReasoningBlockProps) {
  if (!content.trim()) return null;
  return (
    <details className="mt-1 text-xs text-[var(--color-text-muted)]">
      <summary className="cursor-pointer select-none">
        {live && <span className="mr-1 animate-pulse">·</span>}
        Reasoning
      </summary>
      <div className="mt-1 whitespace-pre-wrap border-l-2 border-white/10 pl-2 opacity-80">
        {content}
      </div>
    </details>
  );
}
