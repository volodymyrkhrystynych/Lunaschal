import { stepLabel, type AgentStep } from '@/lib/agentSteps';
import { ReasoningBlock } from './ReasoningBlock';

interface AgentStepsProps {
  steps: AgentStep[];
  /** The model's reasoning for this reply, if any was captured. */
  thinking?: string;
  /** Shown while the run is still going, so the summary can say so. */
  live?: boolean;
}

/**
 * An agent's tool trace, collapsed by default.
 *
 * Collapsed *while streaming* too, not only once saved. The retired web-search
 * tab rendered live steps as a flat list that pushed the reply down the page as
 * it grew, so the thing the user was waiting for moved while they read it. A
 * delegate run is an aside — it gets one line until asked to be more.
 *
 * Reasoning hangs off the same trace, as a second `<details>` *inside* this
 * one: it belongs to the same "what did it do before answering" question the
 * step list answers, and a turn that only thought is exactly the case where
 * there is nothing else to show. With no steps at all it renders on its own
 * rather than behind a wrapper whose only content would be another disclosure
 * saying the same word twice.
 *
 * Shared by the chat delegate and the Ideas discussion, which had each grown
 * their own copy of this `<details>` block and their own `stepLabel`.
 */
export function AgentSteps({
  steps,
  thinking = '',
  live = false,
}: AgentStepsProps) {
  if (steps.length === 0)
    return <ReasoningBlock content={thinking} live={live} />;
  return (
    <details className="mt-1 text-xs text-[var(--color-text-muted)]">
      <summary className="cursor-pointer select-none">
        {live && <span className="mr-1 animate-pulse">·</span>}
        {steps.length} step{steps.length === 1 ? '' : 's'}
        {live ? ' so far' : ''}
      </summary>
      <ul className="mt-1 space-y-0.5">
        {steps.map((step, i) => (
          <li key={i}>· {stepLabel(step)}</li>
        ))}
      </ul>
      <ReasoningBlock content={thinking} live={live} />
    </details>
  );
}
