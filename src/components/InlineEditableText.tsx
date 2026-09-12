import { useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { useDraftState } from '@/hooks/useDraftState';

const BASE_DISPLAY_CLASSES = 'text-sm cursor-text select-none';
const BASE_INPUT_CLASSES =
  'w-full bg-transparent text-[var(--color-text)] text-sm outline-none border-b border-[var(--color-primary)]';

export interface InlineEditableTextProps {
  /** Shown as plain text when not editing, and pre-fills the input the moment
   *  editing starts. */
  value: string;
  /** Whether *this* instance is in edit mode. Controlled by the parent rather
   *  than owned here: `DailyTasks` only lets one row edit at a time (a single
   *  `editingId` for the whole list), while `TodoRow` gives each row its own
   *  flag — that policy has to live above this component. */
  editing: boolean;
  /** Click on the displayed text. */
  onStartEdit: () => void;
  /** Escape, or a blur/Enter whose trimmed text was empty — there is nothing
   *  to save, so this is the only exit in that case. A non-empty blur/Enter
   *  calls `onSave` instead and leaves closing the edit (immediately, or
   *  after a mutation resolves) up to that callback. */
  onStopEdit: () => void;
  /** Fired on blur or Enter with the trimmed text, only when it is non-empty.
   *  Never fired on Escape. The caller decides whether/when to leave edit
   *  mode afterwards (e.g. only once a save mutation succeeds). */
  onSave: (newValue: string) => void;
  /** Extra classes appended after the shared display classes (e.g. a
   *  line-through + muted color for a completed row). */
  displayClassName?: string;
  /** Overrides the shared input classes entirely, for a call site that needs
   *  different styling. Defaults to the classes both current sites share. */
  inputClassName?: string;
  /** Stops the click from bubbling out of the span/input — needed by a row
   *  that itself has an onClick (TodoRow selects the row on click). */
  stopClickPropagation?: boolean;
  /** When provided, the in-progress draft text survives a killed tab via
   *  `useDraftState` (see docs/mobile-draft-recovery.md) instead of the
   *  plain `useState` used when this is omitted. Callers that persist the
   *  `editing` flag itself (`DailyTasks`, `TodoRow`) should also pass this,
   *  or a restored session reopens the row with the old value rather than
   *  the last-typed one. */
  draftKey?: string;
}

/** Always calls both hooks (rules-of-hooks requires a fixed call order) and
 *  picks the durable one only when `draftKey` is set, so a caller that
 *  doesn't need persistence pays for nothing beyond an unused read. */
function useDraft(
  draftKey: string | undefined,
  value: string
): [string, Dispatch<SetStateAction<string>>] {
  const [plain, setPlain] = useState(value);
  const [persisted, setPersisted] = useDraftState(draftKey ?? '', value);
  return draftKey ? [persisted, setPersisted] : [plain, setPlain];
}

/** The "click text → autofocus input → blur/Enter/Escape saves" interaction
 *  shared by `DailyTasks` and `TodoRow`. Escape always reverts to `value`
 *  with no save; a blur/Enter with empty text closes the same way. See
 *  docs/ui-consolidation.md section 5. */
export function InlineEditableText({
  value,
  editing,
  onStartEdit,
  onStopEdit,
  onSave,
  displayClassName = '',
  inputClassName = BASE_INPUT_CLASSES,
  stopClickPropagation = false,
  draftKey,
}: InlineEditableTextProps) {
  const [draft, setDraft] = useDraft(draftKey, value);

  // Seed the draft from the live value the moment edit mode is (re)entered,
  // without waiting for an effect — this instance stays mounted across
  // repeated edit/close cycles (it isn't remounted per edit), so a plain
  // lazy useState initializer would only ever run once.
  const wasEditing = useRef(editing);
  if (editing && !wasEditing.current && draft !== value) {
    setDraft(value);
  }
  wasEditing.current = editing;

  const attemptSave = () => {
    const trimmed = draft.trim();
    if (trimmed) onSave(trimmed);
    else onStopEdit();
  };

  const stop = (e: { stopPropagation: () => void }) => {
    if (stopClickPropagation) e.stopPropagation();
  };

  if (editing) {
    return (
      <input
        autoFocus
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onBlur={attemptSave}
        onKeyDown={e => {
          if (e.key === 'Enter') attemptSave();
          if (e.key === 'Escape') onStopEdit();
        }}
        onClick={stop}
        className={inputClassName}
      />
    );
  }

  return (
    <span
      onClick={e => {
        stop(e);
        onStartEdit();
      }}
      className={`${BASE_DISPLAY_CLASSES} ${displayClassName}`.trim()}
    >
      {value}
    </span>
  );
}
