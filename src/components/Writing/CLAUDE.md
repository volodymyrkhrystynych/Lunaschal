# Writing (`src/components/Writing/`, `backend/routes/writing.py`)

Two-panel layout: left nav (project list + a `WritingNav` with Chapters/Notes/Discussions sections) | full-width center panel that switches on the selected item: chapter → prose editor, note → note editor, discussion → chat view.

**DB tables**: `writing_projects`, `writing_chapters` (ordered by `position`), `writing_context_docs` (typed: `character | outline | worldbuilding | note`). "Notes" in the UI/API are stored in `writing_context_docs` (HTTP paths are `/api/writing/.../notes`; the table name is legacy). Discussions reuse the existing `conversations` + `messages` tables; `conversations.writing_project_id` scopes them to a project, and the general Chat tab filters them out (`writing_project_id IS NULL`). Deleting a project deletes its discussions.

**Chapter/note editors**: plain `<textarea>` (not CodeMirror — prose, not code) with 1.5 s debounced auto-save; chapters add live word count and font-size shortcuts. **Discussions**: full-size chat reusing `/api/chat/stream` unchanged; the frontend assembles a `systemPrompt` from the project plus checked notes. A **Summarize** button distills the transcript into a new note via `backend/ai/writing.py`.
