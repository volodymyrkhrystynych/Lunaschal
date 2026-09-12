import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from 'react';

type Align = 'center' | 'start';

interface ListRowSlots {
  /** Icon, checkbox, or thumbnail rendered before the title/subtitle stack. */
  leading?: ReactNode;
  /** Primary line. Caller supplies its own typography/truncate classes —
   *  this shell only guarantees the ancestor is `min-w-0` so `truncate`
   *  actually has something to clip against. */
  title: ReactNode;
  /** Optional content below the title — one line or several (Email's
   *  subject/snippet/badges stack, a todo's notes, Idea's status chips). */
  subtitle?: ReactNode;
  /** Chips/buttons after the title/subtitle stack. */
  trailing?: ReactNode;
  /** Vertical alignment of leading/body/trailing. 'start' suits rows whose
   *  subtitle can wrap to multiple lines (e.g. a todo's notes) so a leading
   *  checkbox doesn't drift to the vertical center of the wrapped text. */
  align?: Align;
  className?: string;
}

const alignClass: Record<Align, string> = {
  center: 'items-center',
  start: 'items-start',
};

function RowBody({
  leading,
  title,
  subtitle,
  trailing,
}: Pick<ListRowSlots, 'leading' | 'title' | 'subtitle' | 'trailing'>) {
  return (
    <>
      {leading != null && (
        <span className="shrink-0 flex items-center">{leading}</span>
      )}
      <div className="flex-1 min-w-0">
        {title}
        {subtitle != null && subtitle}
      </div>
      {trailing != null && (
        <span className="shrink-0 flex items-center gap-2">{trailing}</span>
      )}
    </>
  );
}

interface ListRowButtonProps extends ListRowSlots {
  onClick: () => void;
  disabled?: boolean;
  id?: string;
  'aria-current'?: ButtonHTMLAttributes<HTMLButtonElement>['aria-current'];
}

/**
 * "Row is a button": the whole row is one clickable target, for rows with no
 * interactive children of their own — `Email/EmailList.tsx`,
 * `Ideas/IdeaList.tsx`, `Learning/Folders.tsx`'s simpler icon+name+actions
 * shape (there the whole row still isn't the click target, so it reaches for
 * `ListRowShell` instead — see that component's doc comment).
 *
 * Use `ListRowShell` instead when the row hosts independent interactive
 * children (a checkbox, inline-editable text, trailing icon buttons) — those
 * can't live inside a native `<button>`.
 */
export function ListRowButton({
  leading,
  title,
  subtitle,
  trailing,
  align = 'center',
  className = '',
  onClick,
  disabled,
  id,
  ...rest
}: ListRowButtonProps) {
  return (
    <button
      type="button"
      id={id}
      onClick={onClick}
      disabled={disabled}
      className={`w-full flex ${alignClass[align]} gap-3 min-h-[44px] text-left disabled:cursor-default ${className}`}
      {...rest}
    >
      <RowBody
        leading={leading}
        title={title}
        subtitle={subtitle}
        trailing={trailing}
      />
    </button>
  );
}

interface ListRowShellProps extends ListRowSlots {
  /** Row-level click, for rows where clicking the background selects the
   *  row (`Tasks/TodoRow.tsx`) but nested controls must be able to stop that
   *  click from bubbling. Omit entirely for a non-interactive wrapper
   *  (`Study/StudyLibrary.tsx`'s unopenable rows). */
  onClick?: () => void;
  id?: string;
}

/**
 * "Row hosts its own controls": a non-button wrapper so the caller can place
 * independent interactive children — a checkbox, an inline-edit trigger,
 * trailing icon buttons — without them fighting a row-level `<button>`.
 * `Tasks/TodoRow.tsx` and `Study/StudyLibrary.tsx` (and, for its trailing
 * select+actions, `Learning/Folders.tsx`) need this rather than
 * `ListRowButton`.
 */
export function ListRowShell({
  leading,
  title,
  subtitle,
  trailing,
  align = 'center',
  className = '',
  onClick,
  id,
  ...rest
}: ListRowShellProps &
  Omit<HTMLAttributes<HTMLDivElement>, 'onClick' | 'id' | 'title'>) {
  return (
    <div
      id={id}
      onClick={onClick}
      className={`flex ${alignClass[align]} gap-3 min-h-[44px] ${className}`}
      {...rest}
    >
      <RowBody
        leading={leading}
        title={title}
        subtitle={subtitle}
        trailing={trailing}
      />
    </div>
  );
}
