# Refactor targets (quick scan, 2026-08-05)

Grep/wc-based pass over the repo since the last cleanup. Not exhaustive — a punch list of concrete duplication/size smells worth a look next time there's cleanup bandwidth.

## Backend

- **Media-upload extension/mime resolution duplicated three times.** `backend/lifestyle/storage.py:39`, `backend/food/storage.py:41`, and `backend/journal/storage.py:114` each hand-roll a near-identical `resolve_ext(mime, filename)`: a mime→ext dict lookup falling back to the filename suffix. Journal's version (`resolve_upload`, `journal/storage.py:89`) is the most complete (also returns `kind`). Worth pulling the mime-lookup shape into `backend/storage.py` (already home to the shared `IdScopedStorage`) parameterized by each feature's accepted-extension set, rather than three copies drifting independently.

- **In-memory progress-registry boilerplate duplicated.** `backend/fanfic/download.py:27` (`_dl_progress` dict + `threading.Lock` + get/start/update/cancel) and `backend/routes/curated_tags.py:13` (`_scan_progress` + `_scan_lock`) implement the same small pattern from scratch. A tiny `ProgressRegistry` helper class would remove ~20 duplicated lines per site and be one thing to get thread-safety right in, instead of two.

- **`backend/routes/learning.py` (973 lines) mixes five unrelated sub-resources** in one file: folder CRUD, MCP-server CRUD, verification/chat, card generation, and the approval queue. `backend/routes/fanfic.py` (812 lines, 29 endpoints) similarly bundles import/download, library listing, folders, site tags, and reading-progress. Both route files could split along the same lines the feature-logic packages already do (`backend/fanfic/`, `backend/learning/` exist as packages — the route files haven't followed).

- **`backend/db/connection.py` has grown to 1053 lines / 37 `_ensure_*` migration functions.** The idempotent-`ALTER TABLE`-guarded-by-`PRAGMA table_info` pattern itself is intentional (per CLAUDE.md) and shouldn't change, but one flat file accumulating a migration per feature is starting to make `git blame`/review noisy. Worth considering a `backend/db/migrations/` package (one module per feature area) if it keeps growing, while keeping `get_db()` as the single call site that runs them in order.

## Frontend

- **`formatDate`/`formatTime` reimplemented locally in five components** instead of living in `src/lib/` — `Journal.tsx`, `Meetings.tsx`, `Food/RecipeList.tsx`, `Fanfic/Library.tsx`, `Food/FoodLog.tsx` each define their own. This directly contradicts the project's own stated convention ("Extractable logic lives in `src/lib/` precisely so it can be tested without jsdom") and is the easiest win here — one extraction + a node-environment test, five call sites updated.

- **`src/hooks/api.ts` is a single 2192-line file.** It's internally well-organized (one `api.*` namespace per feature, per CLAUDE.md), but at this size it's an awkward diff surface — most feature PRs touch it. Splitting into `src/hooks/api/<feature>.ts` re-exported from a barrel `api.ts` would preserve the `api.*` call-site shape while giving each feature its own diffable file.

- **`ChatPanel.tsx` (1029 lines) is one function component with no internal decomposition** — worth checking whether message-list rendering, the input/attachment bar, and streaming-state management can peel into subcomponents or hooks the way `Learning/ReviewSession.tsx` and `Paper/` already do.

- **`Journal.tsx` (1092 lines) bundles the main feed view with four unrelated item-renderer components** (`SavedChatItem`, `JournalPaperItem`, `JournalTaskEventItem`, `JournalFoodItem`) defined inline at file scope. These read like they'd be at home as siblings under a `Journal/` directory (mirroring `Fanfic/`, `Learning/`, `Paper/`), each independently testable.

## Not flagged

`backend/storage.py`'s `IdScopedStorage` abstraction (shared by fanfic/meetings/food/paper) is a good example of duplication already having been factored out — the three `resolve_ext` copies above are the same job half-finished, not a new problem. The `_ensure_*` migration pattern, the per-feature `run_bg` background-thread usage, and the size of `backend/fanfic/download.py` (956 lines, but one coherent state machine) all look intentional rather than accidental and weren't included.
