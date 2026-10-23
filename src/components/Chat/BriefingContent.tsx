import { useEffect, useRef, useState } from 'react';
import { splitServerOddities } from '@/lib/serverOddities';
import { MessageMarkdown } from '../MessageMarkdown';
import { Modal } from '../Modal';

export function BriefingContent({
  content,
  metadata,
}: {
  content: string;
  metadata?: string | null;
}) {
  const report = splitServerOddities(content, metadata);
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (open) close.current?.focus();
  }, [open]);
  function dismiss() {
    setOpen(false);
    trigger.current?.focus();
  }
  if (!report) return <MessageMarkdown content={content} />;
  return (
    <>
      {report.prose && <MessageMarkdown content={report.prose} />}
      <button
        ref={trigger}
        type="button"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
        className="mt-3 w-full rounded border border-white/20 p-3 text-left text-[var(--color-primary)]"
      >
        Server oddities (past day) · {report.items.length} entries — View
        details
      </button>
      {open && (
        <Modal
          onClose={dismiss}
          className="flex max-h-[85dvh] w-full max-w-3xl flex-col rounded-lg bg-[var(--color-surface)] text-[var(--color-text)] shadow-xl"
        >
          <div
            role="dialog"
            aria-label="Server oddities (past day)"
            aria-modal="true"
            onKeyDown={event => {
              // This read-only dialog has one control besides native disclosure summaries.
              if (event.key !== 'Tab') return;
              const controls = Array.from(
                event.currentTarget.querySelectorAll<HTMLElement>(
                  'button, summary, a[href]'
                )
              ).filter(
                element =>
                  !element.closest('details') ||
                  element.closest('details')!.open ||
                  !!element.closest('summary')
              );
              const first = controls[0],
                last = controls[controls.length - 1];
              if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
              } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
              }
            }}
            className="flex min-h-0 flex-col"
          >
            <header className="flex items-center justify-between gap-4 border-b border-white/10 p-4">
              <h2 className="font-bold">Server oddities (past day)</h2>
              <button ref={close} type="button" onClick={dismiss}>
                Close
              </button>
            </header>
            <div className="overflow-y-auto p-4 space-y-3 select-text">
              {report.items.map((item, index) => (
                <details
                  key={index}
                  className="rounded border border-white/20 p-3"
                >
                  <summary className="cursor-pointer break-words">
                    <MessageMarkdown content={item.title} />
                  </summary>
                  <div className="mt-3">
                    <MessageMarkdown
                      content={item.note || 'No explanation was recorded.'}
                    />
                  </div>
                </details>
              ))}
              {report.notices && <MessageMarkdown content={report.notices} />}
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
