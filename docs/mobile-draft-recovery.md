# Mobile text recovery and reload investigation

Production on port 5000 has no Vite HMR client. The inspected frontend has no
explicit page-reload call or service-worker update handler. The cross-view
refresh reported during phone editing has not yet been reproduced or attributed
to a specific browser event.

The selected top-level view was already stored, while Calendar forms, Journal
composition/editing, chapter commentary and several Lifestyle inputs existed
only in React state. A reload could therefore look like returning to the same
screen with all unsaved text gone. Lifestyle also recreates the calorie and
weight cards when refreshed data moves them into or out of today's priorities.

`useDraftState` stores small JSON drafts synchronously when their setters run,
including functional updates from transcription callbacks. It restores on mount
and isolates chapter/event drafts by entity. Returning to the initial value
removes the stored value. The existing workout draft store remains in use.
Storage failures leave editing operational, but recovery is unavailable when
the browser blocks storage or its quota is exhausted. Drafts are local to this
browser/origin and do not synchronize between devices.

This change covers text and the state needed to reopen its forms. It does not
extend attachment persistence or change the offline mutation queue's save
semantics. Already-submitted edits use that existing queue. It cannot restore
text lost before the change was loaded.

Settings → Logs → Browser reload history shows the last 80 lifecycle signals
from this tab, retained in sessionStorage across reloads. Boot identifiers
distinguish a new document execution from a React shell remount. Navigation
type, `wasDiscarded` where available, page visibility, pageshow/pagehide,
auth-gate changes and counts of uncaught errors help narrow the trigger.
No typed content, entity IDs, URLs or error messages are recorded or uploaded.
The browser may not report the reason for a reload; missing pagehide is not
proof of a crash. Development StrictMode deliberately mounts effects twice.

Regression tests recreate the editors without unload events, recover the last
keystroke, switch chapter keys, exercise storage failures and verify successful
save/explicit Journal cancellation cleanup. A calorie-card test recreates the
card in a different part of the layout with an unfinished description.
