CREATE TABLE IF NOT EXISTS journal_entries (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    raw_content TEXT,
    title TEXT,
    tags TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Audio and photo attachments on a journal entry. The files themselves live
-- under ./data/journal/<attachment_id>/ (backend/journal/storage.py), never as
-- blobs, matching the fanfic/meetings/lifestyle media roots.
CREATE TABLE IF NOT EXISTS journal_attachments (
    id TEXT PRIMARY KEY,
    entry_id TEXT NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,          -- 'audio' | 'image'
    -- What the attachment is about, named by the user. Defaults to the uploaded
    -- filename so a list of attachments is never a list of blank rows.
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    mime TEXT,
    size INTEGER,
    position INTEGER NOT NULL DEFAULT 0,
    -- AI-derived text, and never generated on upload: transcription/captioning
    -- is opt-in per attachment. Holds the transcript for audio and the caption
    -- for an image — one column, so the status/polling path is shared.
    transcript TEXT,
    -- 'idle' | 'running' | 'done' | 'error'. Reset to 'idle' at startup for any
    -- row left 'running' by a crash (backend/db/connection.py).
    transcript_status TEXT NOT NULL DEFAULT 'idle',
    transcript_error TEXT,
    -- Non-speech audio/video description ("a dog barking, a door slamming"),
    -- via a separate audio-capable model (backend/ai/audio_description.py) —
    -- kept apart from `transcript` because a spoken recording wants both the
    -- words said AND the ambient sound, not one or the other.
    description TEXT,
    description_status TEXT NOT NULL DEFAULT 'idle',
    description_error TEXT,
    -- EXIF-derived capture location for image attachments (backend/food/exif.py's
    -- extract_photo_meta, same helper the food log uses). NULL when the photo
    -- carries no GPS EXIF, or for non-image attachments.
    latitude REAL,
    longitude REAL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_journal_attachments_entry
    ON journal_attachments(entry_id, position);

-- A voice clip recorded via the STT listener's Journal hotkey, before it
-- becomes an entry. The files live under ./data/journal_drafts/<draft_id>/
-- (backend/journal/voice_drafts.py) — a separate root from journal_attachments
-- above, since a draft has no entry_id yet. Several local STT backends
-- transcribe the same clip in the background; their outputs are cross-checked
-- and polished into one entry by backend/ai/journal.py's merge_voice_draft.
CREATE TABLE IF NOT EXISTS journal_voice_drafts (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    mime TEXT,
    size INTEGER,
    -- 'processing' | 'done' | 'error'. Reset to 'error' at startup for any row
    -- left 'processing' by a crash (backend/db/connection.py) — unlike an idle
    -- attachment transcript, there is no button to re-arm; the user retries
    -- explicitly from the drafts dropdown.
    status TEXT NOT NULL DEFAULT 'processing',
    error TEXT,
    -- JSON array of {backend, text} | {backend, error} — one entry per STT
    -- backend that ran, kept for the drafts dropdown even after promotion to
    -- an entry. Not surfaced as journal_entries.raw_content, which stays a
    -- single plain transcript so the existing manual Polish button keeps
    -- working unmodified.
    candidates TEXT,
    entry_id TEXT REFERENCES journal_entries(id),
    created_at INTEGER NOT NULL,
    completed_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_journal_voice_drafts_status
    ON journal_voice_drafts(status, created_at);

CREATE TABLE IF NOT EXISTS calendar_events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    date TEXT NOT NULL,
    time TEXT,
    end_time TEXT,
    -- Explicitly all-day, as opposed to merely untimed. Both render without a
    -- clock, but only this one says the user meant the whole day — legacy rows
    -- with a NULL time predate the flag and are left as untimed.
    all_day INTEGER NOT NULL DEFAULT 0,
    tags TEXT,
    journal_id TEXT REFERENCES journal_entries(id),
    created_at INTEGER NOT NULL,
    -- Recurrence rule. NULL repeat_freq = a one-off event; `date` is the anchor.
    repeat_freq TEXT,          -- 'daily' | 'weekly' | 'monthly' | 'yearly'
    repeat_interval INTEGER,   -- every N units (default 1)
    repeat_byweekday TEXT,     -- CSV of 0-6, Sunday=0 (matches JS getDay and the UI grid)
    repeat_until TEXT,         -- 'YYYY-MM-DD' inclusive; NULL = forever
    -- Set when a series was split by a "this and future" edit: this row starts
    -- where the referenced one was capped. A breadcrumb only, like
    -- learning_cards.revised_from.
    split_from TEXT REFERENCES calendar_events(id) ON DELETE SET NULL,
    -- AI-assigned category tags (leisure/work/exercise/family/outside/indoors,
    -- backend/ai/calendar.py), JSON array, separate from the free-text `tags`
    -- column above so a user-typed pill can never collide with a classifier
    -- result. classified_at IS NULL = still pending, same idiom as
    -- emails.classified_at.
    category_tags TEXT,
    classified_at INTEGER,
    classification_error TEXT
);

-- Per-occurrence edits to a recurring series: a template row plus dated
-- exception rows joined at read time (same shape as daily_task_completions).
CREATE TABLE IF NOT EXISTS calendar_event_exceptions (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
    date TEXT NOT NULL,        -- the occurrence date being modified
    action TEXT NOT NULL CHECK(action IN ('skip','move')),
    new_date TEXT,
    new_time TEXT,
    new_end_time TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(event_id, date)
);

CREATE TABLE IF NOT EXISTS calendar_journal_links (
    id TEXT PRIMARY KEY,
    calendar_event_id TEXT NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
    journal_entry_id TEXT NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL
);

-- Manually-set wake/sleep times for a day. A row exists ONLY for a day the user
-- corrected by hand: the times are otherwise derived on read from when they were
-- active (backend/sleep.py), so there is nothing to keep in sync. The two
-- columns are independently nullable, so one end can be manual while the other
-- stays derived. `date` is the 4am-anchored day key (backend/day_boundary.py),
-- not a calendar date -- a 01:30 bedtime belongs to the day that started the
-- previous morning.
CREATE TABLE IF NOT EXISTS sleep_logs (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL UNIQUE,
    wake_at INTEGER,
    sleep_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp_servers (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    transport TEXT NOT NULL DEFAULT 'stdio' CHECK(transport IN ('stdio','http')),
    command TEXT,
    args TEXT,
    env TEXT,
    url TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_folders (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    evidence_provider_id TEXT REFERENCES mcp_servers(id) ON DELETE SET NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_cards (
    id TEXT PRIMARY KEY,
    folder_id TEXT REFERENCES learning_folders(id) ON DELETE SET NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','active','retired')),
    tags TEXT,
    claims TEXT,
    answer_embedding BLOB,
    source_type TEXT,
    source_id TEXT,
    derived_from TEXT REFERENCES learning_cards(id) ON DELETE SET NULL,
    revised_from TEXT REFERENCES learning_cards(id) ON DELETE SET NULL,
    generation_context TEXT,
    fsrs_state TEXT,
    due INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_learning_cards_due ON learning_cards(state, due);
CREATE INDEX IF NOT EXISTS idx_learning_cards_folder ON learning_cards(folder_id, state);

CREATE TABLE IF NOT EXISTS learning_reviews (
    id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES learning_cards(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 4),
    suggested_rating INTEGER,
    user_answer TEXT,
    coverage TEXT,
    answer_mode TEXT CHECK(answer_mode IN ('typed','voice','self')),
    review_log TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_learning_reviews_card ON learning_reviews(card_id, created_at);

-- Answered-but-not-yet-rated cards: the live state of a review session, so
-- leaving the view (or reloading) doesn't make you answer the same cards again.
-- A row is deleted the moment its rating is committed to learning_reviews, so
-- this table only ever holds open attempts — never review history.
CREATE TABLE IF NOT EXISTS learning_attempts (
    id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL UNIQUE REFERENCES learning_cards(id) ON DELETE CASCADE,
    mode TEXT NOT NULL CHECK(mode IN ('answered','skipped')),
    answer TEXT,
    answer_mode TEXT CHECK(answer_mode IN ('typed','voice','self')),
    grade_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(grade_status IN ('pending','done','error','skipped')),
    coverage TEXT,
    suggested_rating INTEGER,
    normalized_answer TEXT,
    -- Speech mode's live toggle state at submit time, not at grade time — a
    -- card answered before the toggle was on never gets a spoken blurb, even
    -- if grading finishes after it's switched on. Re-answering re-stamps it.
    speech_requested INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_revisions (
    id TEXT PRIMARY KEY,
    old_card_id TEXT REFERENCES learning_cards(id) ON DELETE SET NULL,
    new_card_id TEXT NOT NULL REFERENCES learning_cards(id) ON DELETE CASCADE,
    trigger_type TEXT NOT NULL CHECK(trigger_type IN ('manual_edit','web_verification')),
    old_answer TEXT NOT NULL,
    new_answer TEXT NOT NULL,
    diff TEXT,
    is_semantic INTEGER NOT NULL,
    sources TEXT,
    note TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_learning_revisions_new ON learning_revisions(new_card_id);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    metadata TEXT,
    -- 'streaming' while a background run (backend/delegate/runs.py) is still
    -- generating this reply, so a dropped client connection doesn't lose it.
    status TEXT NOT NULL DEFAULT 'done' CHECK(status IN ('streaming','done','error')),
    error TEXT,
    -- What was actually dictated, before the transcript-correction pass and
    -- before the user edited it in the composer. NULL when the message was
    -- typed. Never overwritten -- the journal_entries raw_content contract.
    raw_content TEXT,
    created_at INTEGER NOT NULL,
    -- When generation actually stopped. `created_at` is stamped when the run
    -- *starts*, which for a local model is minutes before the reply exists --
    -- so a question and its answer both read "7:56 PM" and the label says
    -- nothing. NULL on user messages and on rows predating the column, which
    -- is why the UI falls back to created_at rather than showing a blank.
    finished_at INTEGER
);

-- Photos attached to a chat message. The chat model is text-only
-- (llama/presets.ini sets mmproj-auto = false on [qwen36]), so `description` --
-- written by the CPU-only omni model in backend/ai/images.py -- is how the
-- picture actually reaches the conversation.
CREATE TABLE IF NOT EXISTS chat_attachments (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    -- NULL until the message is sent. The photo is uploaded (and reading it
    -- starts) while the user is still dictating, before any message row exists.
    message_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    mime TEXT,
    description TEXT,
    description_status TEXT,
    description_error TEXT,
    -- Where the device was when the photo was attached. A *fallback* for the
    -- photo's own EXIF GPS, which iOS strips whenever an image goes through the
    -- clipboard or a share sheet rather than being handed over as the original
    -- file — exactly the paths this composer supports.
    latitude REAL,
    longitude REAL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_attachments_message ON chat_attachments(message_id);
CREATE INDEX IF NOT EXISTS idx_chat_attachments_conversation ON chat_attachments(conversation_id);

-- One free-text document the assistant keeps up to date about the user: proper
-- names speech-to-text keeps mangling, standing preferences, things worth
-- carrying between conversations. Read into every chat system prompt.
CREATE TABLE IF NOT EXISTS user_memory (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    content TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL
);

-- Copy-on-write, the wiki_revisions/learning_revisions pattern. The assistant
-- writes to the memory without asking, so every change has to be inspectable and
-- undoable; `content` is the document as it stood BEFORE the change.
CREATE TABLE IF NOT EXISTS user_memory_revisions (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    note TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_memory_revisions_created ON user_memory_revisions(created_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY DEFAULT 1,
    ai_provider TEXT DEFAULT 'openai',
    ai_model TEXT,
    openai_api_key TEXT,
    google_api_key TEXT,
    llama_url TEXT DEFAULT 'http://localhost:8080',
    llama_model TEXT,
    network_code TEXT,
    stt_backend TEXT,
    tts_backend TEXT,
    whisper_model TEXT,
    stt_device TEXT,
    voice_pipeline_enabled INTEGER DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_journal_created ON journal_entries(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_calendar_date ON calendar_events(date);
CREATE INDEX IF NOT EXISTS idx_calendar_exc_event ON calendar_event_exceptions(event_id);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
-- backend/sleep.py scans messages by time across every conversation, which the
-- composite index above can't serve (its leading column is the conversation).
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);

CREATE TABLE IF NOT EXISTS writing_projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS writing_chapters (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES writing_projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS writing_context_docs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES writing_projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    doc_type TEXT NOT NULL DEFAULT 'note',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_writing_chapters_project ON writing_chapters(project_id, position);
CREATE INDEX IF NOT EXISTS idx_writing_context_docs_project ON writing_context_docs(project_id);

CREATE TABLE IF NOT EXISTS daily_tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_task_completions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES daily_tasks(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(task_id, date)
);

CREATE TABLE IF NOT EXISTS todos (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    completed_at INTEGER,
    list TEXT NOT NULL DEFAULT 'todo',
    notes TEXT,
    due INTEGER,
    repeat_interval INTEGER,
    repeat_unit TEXT,
    priority INTEGER NOT NULL DEFAULT 3,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Append-only log of task lifecycle events (completions, deletions) surfaced as
-- small notifications in the Journal feed. `title` is a snapshot so a deleted
-- task's event survives; `ref_id` links back to the todo/daily-task so an
-- un-complete can retract its matching completion event.
CREATE TABLE IF NOT EXISTS task_events (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    ref_id TEXT,
    task_list TEXT,
    detail TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_events_created ON task_events(created_at);

CREATE TABLE IF NOT EXISTS curated_tags (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS journal_entry_curated_tags (
    entry_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    PRIMARY KEY(entry_id, tag_id),
    FOREIGN KEY(entry_id) REFERENCES journal_entries(id) ON DELETE CASCADE,
    FOREIGN KEY(tag_id) REFERENCES curated_tags(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ject_tag ON journal_entry_curated_tags(tag_id);

CREATE TABLE IF NOT EXISTS recipes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT,
    source_url TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recipes_created ON recipes(created_at DESC);

CREATE TABLE IF NOT EXISTS recipe_media (
    id TEXT PRIMARY KEY,
    recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                   -- 'image' | 'video'
    path TEXT NOT NULL,
    mime TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recipe_media_recipe ON recipe_media(recipe_id);

-- Food log: what was eaten, where, whether it was good, plus photos/videos.
-- Shown in both the Food tab and the Journal feed; stored once here.
CREATE TABLE IF NOT EXISTS food_entries (
    id TEXT PRIMARY KEY,
    raw_content TEXT,                     -- exactly as typed/spoken
    dish TEXT,                            -- AI-extracted or manual
    place TEXT,
    notes TEXT,                           -- cleaned commentary
    rating INTEGER,                       -- 1..5, nullable
    tags TEXT,                            -- JSON array (see backend/tags.py)
    recipe_id TEXT REFERENCES recipes(id) ON DELETE SET NULL,
    latitude REAL,                        -- device GPS captured at log time
    longitude REAL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_food_entries_created ON food_entries(created_at DESC);

CREATE TABLE IF NOT EXISTS food_media (
    id TEXT PRIMARY KEY,
    entry_id TEXT NOT NULL REFERENCES food_entries(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                   -- 'image' | 'video'
    path TEXT NOT NULL,
    mime TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_food_media_entry ON food_media(entry_id);

CREATE TABLE IF NOT EXISTS transcriptions (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    source TEXT NOT NULL,
    app TEXT,
    detail TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transcriptions_created ON transcriptions(created_at DESC);

CREATE TABLE IF NOT EXISTS fics (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    source_type TEXT NOT NULL CHECK(source_type IN ('xenforo','epub','docx','pdf')),
    source_url TEXT,
    site TEXT,
    thread_id TEXT,
    description TEXT,
    cover_path TEXT,
    word_count INTEGER NOT NULL DEFAULT 0,
    chapter_count INTEGER NOT NULL DEFAULT 0,
    download_status TEXT NOT NULL DEFAULT 'complete'
        CHECK(download_status IN ('downloading','complete','error')),
    download_error TEXT,
    update_pending INTEGER NOT NULL DEFAULT 0,
    deep_pending INTEGER NOT NULL DEFAULT 0,
    last_read_chapter_id TEXT,
    last_checked_at INTEGER,
    rating INTEGER CHECK(rating BETWEEN 1 AND 5),
    review TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_fics_site_thread ON fics(site, thread_id)
    WHERE thread_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fics_created ON fics(created_at DESC);

CREATE TABLE IF NOT EXISTS fic_chapters (
    id TEXT PRIMARY KEY,
    fic_id TEXT NOT NULL REFERENCES fics(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'threadmarks',
    content_html TEXT NOT NULL,
    content_text TEXT NOT NULL,
    source_url TEXT,
    source_post_id TEXT,
    word_count INTEGER NOT NULL DEFAULT 0,
    posted_at INTEGER,
    edited_at INTEGER,
    created_at INTEGER NOT NULL,
    UNIQUE(fic_id, source_post_id)
);

CREATE INDEX IF NOT EXISTS idx_fic_chapters_fic ON fic_chapters(fic_id, category, position);

CREATE TABLE IF NOT EXISTS journal_entry_fic_refs (
    id TEXT PRIMARY KEY,
    journal_entry_id TEXT NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
    fic_id TEXT NOT NULL REFERENCES fics(id) ON DELETE CASCADE,
    chapter_id TEXT REFERENCES fic_chapters(id) ON DELETE SET NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(journal_entry_id, fic_id, chapter_id)
);

CREATE INDEX IF NOT EXISTS idx_jefr_fic ON journal_entry_fic_refs(fic_id);

CREATE TABLE IF NOT EXISTS site_cookies (
    domain TEXT PRIMARY KEY,
    cookie TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fanfic_watched_scans (
    domain TEXT PRIMARY KEY,
    next_page INTEGER NOT NULL DEFAULT 1,
    found INTEGER NOT NULL DEFAULT 0,
    imported INTEGER NOT NULL DEFAULT 0,
    already_in_library INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fic_folders (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fic_folder_items (
    folder_id TEXT NOT NULL REFERENCES fic_folders(id) ON DELETE CASCADE,
    fic_id TEXT NOT NULL REFERENCES fics(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (folder_id, fic_id)
);

CREATE INDEX IF NOT EXISTS idx_ffi_fic ON fic_folder_items(fic_id);

CREATE TABLE IF NOT EXISTS fic_site_tags (
    fic_id TEXT NOT NULL REFERENCES fics(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (fic_id, name)
);

CREATE INDEX IF NOT EXISTS idx_fst_name ON fic_site_tags(name);

CREATE TABLE IF NOT EXISTS fic_chapter_reads (
    chapter_id TEXT PRIMARY KEY REFERENCES fic_chapters(id) ON DELETE CASCADE,
    fic_id TEXT NOT NULL REFERENCES fics(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fcr_fic ON fic_chapter_reads(fic_id);

CREATE TABLE IF NOT EXISTS fic_bookmarks (
    id TEXT PRIMARY KEY,
    fic_id TEXT NOT NULL REFERENCES fics(id) ON DELETE CASCADE,
    chapter_id TEXT NOT NULL REFERENCES fic_chapters(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK(type IN ('favorite', 'continue')),
    scroll_position REAL NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fic_bookmarks_fic ON fic_bookmarks(fic_id, created_at);
-- At most one continue bookmark per fic; creating a new one replaces the old.
CREATE UNIQUE INDEX IF NOT EXISTS idx_fic_bookmarks_continue
    ON fic_bookmarks(fic_id) WHERE type = 'continue';

CREATE TABLE IF NOT EXISTS newspaper_frontpages (
    id TEXT PRIMARY KEY,
    paper TEXT NOT NULL CHECK(paper IN ('toronto-star','nyt')),
    date TEXT NOT NULL,
    image_path TEXT NOT NULL,
    source_url TEXT,
    created_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_newspaper_frontpages_paper_date ON newspaper_frontpages(paper, date);
CREATE INDEX IF NOT EXISTS idx_newspaper_frontpages_date ON newspaper_frontpages(date DESC);

CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'recording'
        CHECK(status IN ('recording','transcribing','done','error')),
    phase TEXT NOT NULL DEFAULT 'recording',
    source TEXT NOT NULL DEFAULT 'live' CHECK(source IN ('live','upload')),
    error TEXT,
    segments TEXT,
    transcript_text TEXT,
    speaker_names TEXT,
    summary TEXT,
    notes TEXT NOT NULL DEFAULT '',
    duration_seconds REAL,
    whisper_model TEXT NOT NULL DEFAULT 'large-v3',
    whisper_device TEXT NOT NULL DEFAULT 'cpu',
    pause_requested INTEGER NOT NULL DEFAULT 0,
    mic_offset_seconds REAL NOT NULL DEFAULT 0,
    mic_segments_partial TEXT,
    system_offset_seconds REAL NOT NULL DEFAULT 0,
    system_segments_partial TEXT,
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_meetings_created ON meetings(created_at DESC);

-- Review-scheduling state for Notebook files. Notebook content itself lives
-- on disk (see backend/routes/notebook.py); this table only holds the FSRS
-- state for files the user has opted into spaced-repetition review, keyed by
-- their path relative to the notebook root. Kept in sync with renames/deletes
-- by backend/routes/notebook.py's on_rename/on_delete hooks into files.py.
CREATE TABLE IF NOT EXISTS notebook_review_state (
    path TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    fsrs_state TEXT,
    due INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notebook_review_due ON notebook_review_state(enabled, due);

-- Tracks which diary/YYYY-MM-DD.md notes have already been promoted into a
-- Journal entry (backend/notebook_diary_scheduler.py), so a catch-up scan
-- after the app was closed overnight never double-promotes one.
CREATE TABLE IF NOT EXISTS notebook_diary_promotions (
    date TEXT PRIMARY KEY,
    journal_entry_id TEXT NOT NULL,
    promoted_at INTEGER NOT NULL
);

-- Notes to self: freeform notes jotted from chat, resurfaced on a fixed
-- 1/2/4/7/14-day ladder (backend/notes.py) rather than FSRS — there's no
-- correctness signal to grade, just seen-or-not, so nothing for a
-- memory-decay model to fit, and 14 days is a deliberate ceiling FSRS has no
-- native knob for.
CREATE TABLE IF NOT EXISTS notes_to_self (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    interval_days INTEGER NOT NULL DEFAULT 1,
    due INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_to_self_due ON notes_to_self(due);

-- Copy-on-write edit history, the user_memory_revisions shape: `content` is
-- what the note said *before* the edit that produced this row.
CREATE TABLE IF NOT EXISTS note_to_self_revisions (
    id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL REFERENCES notes_to_self(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_note_to_self_revisions_note ON note_to_self_revisions(note_id, created_at DESC);

-- Paper: freeform handwriting/drawing documents (Apple Pencil on iPad). A
-- "paper" is a document; its ordered "pages" each hold vector strokes (JSON,
-- the source of truth for editing) plus a rendered PNG snapshot on disk (used
-- for the grid thumbnail / quick view). See backend/paper/storage.py.
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    -- When set, the user has flagged this paper to move out of the Paper
    -- explorer and into the Journal. The move happens lazily once the next 4am
    -- boundary passes (see backend/routes/paper.py); until then it stays in the
    -- explorer, marked pending, and the flag can be toggled back off.
    archive_requested_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_pages (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    strokes TEXT NOT NULL DEFAULT '[]',
    width INTEGER,
    height INTEGER,
    image_path TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paper_pages_paper ON paper_pages(paper_id, position);

-- A picture pasted onto a page. The file lives on disk beside the page's
-- snapshot (never as a blob); this row is only its placement. Geometry is in
-- the page's A4 coordinate space, the same one strokes use — see src/lib/paper.ts.
CREATE TABLE IF NOT EXISTS paper_page_images (
    id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL REFERENCES paper_pages(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    -- Top-left corner and size of the unrotated box; the image rotates and
    -- flips about its own centre.
    x REAL NOT NULL,
    y REAL NOT NULL,
    width REAL NOT NULL,
    height REAL NOT NULL,
    -- Degrees clockwise. The client snaps to quarter turns, but the column is free-form
    -- so an existing angle survives a future finer-grained editor.
    rotation REAL NOT NULL DEFAULT 0,
    -- Mirrored left-to-right about its own centre. Combined with rotation this
    -- reaches every orientation, so there is deliberately no vertical flip.
    flipped INTEGER NOT NULL DEFAULT 0,
    -- A locked image is still drawn, but can't be selected, moved, resized or
    -- rotated — so writing over a photo can't nudge it out from under the pen.
    locked INTEGER NOT NULL DEFAULT 0,
    -- Draw order within the page, low to high.
    position INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paper_page_images_page
    ON paper_page_images(page_id, position);

-- --- Lifestyle tab: workouts, body weight, selfies, calories -----------------
-- See docs/lifestyle-tab.md. Tasks are deliberately absent: the tab renders
-- daily_tasks and todos directly rather than forking a second copy of either.
-- (The `chores` list those todos used to be split across is retired — the rows
-- were folded into 'todo' by _merge_chores_into_todos in backend/db/connection.py.)

-- One logged training session. `raw_text` is the freeform text exactly as typed
-- and is never overwritten — the AI-parsed exercises/sets below hang off it, so
-- a bad parse can be re-run (POST .../reparse) without losing what was written.
-- `date` is the user's local 'YYYY-MM-DD' day (what the heatmap grids on), not a
-- timestamp, so it stays out of TIMESTAMP_COLS.
CREATE TABLE IF NOT EXISTS workout_sessions (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    location_type TEXT NOT NULL,          -- see backend/lifestyle/activity.py
    duration_minutes INTEGER,
    intensity_rating INTEGER,             -- self-rated 1-5 stars, each with a
                                          -- written meaning (1 "not intense
                                          -- whatsoever" … 5 "I am going ham").
                                          -- Was a 1-10 RPE; old rows were folded
                                          -- once by _migrate_workout_intensity_to_stars.
    raw_text TEXT,
    notes TEXT,
    parse_status TEXT NOT NULL DEFAULT 'pending',  -- pending|done|error|skipped
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workout_sessions_date ON workout_sessions(date DESC);

-- `name_raw` is what the user wrote ("curls"); `name_canonical` is what it was
-- folded onto ("bicep curl") so the progression chart can group across spellings.
CREATE TABLE IF NOT EXISTS workout_exercises (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES workout_sessions(id) ON DELETE CASCADE,
    name_raw TEXT NOT NULL,
    name_canonical TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_workout_exercises_session ON workout_exercises(session_id, position);
CREATE INDEX IF NOT EXISTS idx_workout_exercises_canonical ON workout_exercises(name_canonical);

CREATE TABLE IF NOT EXISTS workout_sets (
    id TEXT PRIMARY KEY,
    exercise_id TEXT NOT NULL REFERENCES workout_exercises(id) ON DELETE CASCADE,
    weight REAL,
    reps INTEGER,
    set_order INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_workout_sets_exercise ON workout_sets(exercise_id, set_order);

-- Manual weigh-ins, at most one per day (re-logging the same day overwrites).
CREATE TABLE IF NOT EXISTS body_weight_logs (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL UNIQUE,
    weight REAL NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- One selfie per day; the image itself lives under ./data/lifestyle/<id>/.
CREATE TABLE IF NOT EXISTS lifestyle_selfies (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    mime TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS calorie_logs (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    calories INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calorie_logs_date ON calorie_logs(date);

-- Hourly weather, one row per (day_key, hour_ts). day_key is the 4am-anchored
-- day from backend/day_boundary.py, not the raw calendar date Open-Meteo
-- returns — see backend/weather/sync.py. Rows are upserted in place as the day
-- progresses: is_actual flips from 0 to 1 once hour_ts has passed, and every
-- resync (a fresh geolocation fix, or the day's first tab visit) overwrites
-- forecast values with fresher ones for the location known at sync time.
CREATE TABLE IF NOT EXISTS lifestyle_weather_hours (
    id TEXT PRIMARY KEY,
    day_key TEXT NOT NULL,
    hour_ts INTEGER NOT NULL,
    weather_code INTEGER NOT NULL,
    temperature_c REAL NOT NULL,
    wet_bulb_c REAL,
    humidity_pct REAL,
    is_actual INTEGER NOT NULL DEFAULT 0,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    location_source TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(day_key, hour_ts)
);

CREATE INDEX IF NOT EXISTS idx_weather_hours_day ON lifestyle_weather_hours(day_key);

-- Append-only log of the location used at each weather sync — kept separate
-- from lifestyle_weather_hours because a resync overwrites that table's rows
-- for the whole day (there's no continuous GPS trail to preserve per-hour
-- location history), so this is the only record of "where the app thought
-- the user was" at a given moment.
CREATE TABLE IF NOT EXISTS lifestyle_weather_locations (
    id TEXT PRIMARY KEY,
    day_key TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    source TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_weather_locations_day ON lifestyle_weather_locations(day_key, created_at);

-- Ideas: the app's own feature backlog, developed with the research agent.
-- raw_content is what was spoken/typed and is never overwritten; content is the
-- AI-cleaned prose, following the journal_entries split.
CREATE TABLE IF NOT EXISTS ideas (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    raw_content TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new'
        CHECK(status IN ('new','researching','ready','planned','building','shipped','parked')),
    tags TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ideas_updated ON ideas(updated_at DESC);

-- A sketch is a Paper *page*, not a whole paper: one page is one diagram. The
-- caption is not decoration — vision is off by default in this project (both
-- presets set mmproj-auto = false, see backend/ai/images.py), so the caption is
-- what the research agent actually reads. The image is for the human.
CREATE TABLE IF NOT EXISTS idea_sketches (
    id TEXT PRIMARY KEY,
    idea_id TEXT NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    page_id TEXT NOT NULL REFERENCES paper_pages(id) ON DELETE CASCADE,
    caption TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_idea_sketches_idea ON idea_sketches(idea_id, position);

-- A nightly, machine-generated picture of what the app currently is. `facts`
-- is deterministic extraction (backend/research/repo_facts.py); only
-- `change_summary` comes from the model, and it is nullable on purpose — a
-- failed summary must never cost us the facts, which are the actual product.
CREATE TABLE IF NOT EXISTS repo_snapshots (
    id TEXT PRIMARY KEY,
    git_sha TEXT,
    git_branch TEXT,
    facts TEXT NOT NULL DEFAULT '{}',
    digest TEXT NOT NULL DEFAULT '',
    change_summary TEXT,
    route_count INTEGER NOT NULL DEFAULT 0,
    table_count INTEGER NOT NULL DEFAULT 0,
    component_count INTEGER NOT NULL DEFAULT 0,
    warnings TEXT,
    prev_snapshot_id TEXT REFERENCES repo_snapshots(id) ON DELETE SET NULL,
    generated_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_repo_snapshots_generated ON repo_snapshots(generated_at DESC);

-- The LLM wiki: agent-written, agent-updated articles about the problem spaces
-- the ideas live in. Revisions are append-only copy-on-write, following
-- learning_revisions — the agent must never be able to silently rewrite
-- something the user relied on.
CREATE TABLE IF NOT EXISTS wiki_articles (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    sources TEXT,
    tags TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    -- A locked article is the user's; the agent proposes changes instead.
    locked INTEGER NOT NULL DEFAULT 0,
    last_researched_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wiki_articles_updated ON wiki_articles(updated_at DESC);

CREATE TABLE IF NOT EXISTS wiki_revisions (
    id TEXT PRIMARY KEY,
    article_id TEXT NOT NULL REFERENCES wiki_articles(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    diff TEXT,
    author TEXT NOT NULL DEFAULT 'agent' CHECK(author IN ('agent','user')),
    note TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wiki_revisions_article ON wiki_revisions(article_id, revision DESC);

CREATE TABLE IF NOT EXISTS idea_wiki_links (
    idea_id TEXT NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    article_id TEXT NOT NULL REFERENCES wiki_articles(id) ON DELETE CASCADE,
    relevance REAL NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (idea_id, article_id)
);

-- The agent's judgement about an idea, kept append-only and tied to the repo
-- snapshot it was made against — that pairing is what lets the UI say an
-- assessment has gone stale rather than presenting an old verdict as current.
CREATE TABLE IF NOT EXISTS idea_assessments (
    id TEXT PRIMARY KEY,
    idea_id TEXT NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    snapshot_id TEXT REFERENCES repo_snapshots(id) ON DELETE SET NULL,
    verdict TEXT NOT NULL DEFAULT 'no' CHECK(verdict IN ('no','partial','yes')),
    confidence REAL NOT NULL DEFAULT 0,
    rationale TEXT NOT NULL DEFAULT '',
    -- JSON [{kind, ref, file, line}] — chosen by index from a list we built,
    -- so the model cannot cite a file that does not exist.
    evidence TEXT NOT NULL DEFAULT '[]',
    on_roadmap TEXT,
    effort TEXT,
    model TEXT,
    assessed_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_idea_assessments_idea ON idea_assessments(idea_id, assessed_at DESC);

-- Decisions the idea still needs from the user. Upserted by question_key so a
-- re-run never resurrects something already answered.
CREATE TABLE IF NOT EXISTS idea_questions (
    id TEXT PRIMARY KEY,
    idea_id TEXT NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    question_key TEXT NOT NULL,
    why TEXT,
    options TEXT,
    answer TEXT,
    answered_at INTEGER,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','answered','dismissed')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_idea_questions_key ON idea_questions(idea_id, question_key);

-- Versioned plan documents. Append-only: regenerating never destroys the
-- version you may already have handed to a coding agent.
CREATE TABLE IF NOT EXISTS idea_plans (
    id TEXT PRIMARY KEY,
    idea_id TEXT NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 1,
    content TEXT NOT NULL,
    spec TEXT NOT NULL DEFAULT '{}',
    model TEXT,
    snapshot_id TEXT REFERENCES repo_snapshots(id) ON DELETE SET NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_idea_plans_idea ON idea_plans(idea_id, version DESC);

-- Practice tab: per-snippet drill progress. snippet_id is a stable slug from
-- the static bank in backend/practice/snippets.py, not a generated row id.
CREATE TABLE IF NOT EXISTS practice_progress (
    snippet_id TEXT PRIMARY KEY,
    attempts_count INTEGER NOT NULL DEFAULT 0,
    last_wpm REAL,
    last_accuracy REAL,
    best_wpm REAL,
    best_accuracy REAL,
    last_practiced_at INTEGER,
    -- Blind (from-memory) drill counters. Kept on the same row because
    -- backend/practice/modes.py decides the next drill from the *mix* of the
    -- two, so a mode decision must never need a second lookup.
    recall_attempts_count INTEGER NOT NULL DEFAULT 0,
    recall_passes INTEGER NOT NULL DEFAULT 0,
    last_recall_passed INTEGER,
    last_recall_at INTEGER,
    updated_at INTEGER NOT NULL
);

-- Append-only history of every speed drill attempt, for stats trends.
CREATE TABLE IF NOT EXISTS practice_attempts (
    id TEXT PRIMARY KEY,
    snippet_id TEXT NOT NULL,
    wpm REAL NOT NULL,
    accuracy REAL NOT NULL,
    error_count INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_practice_attempts_snippet ON practice_attempts(snippet_id, created_at DESC);

-- Append-only history of blind drill attempts. A separate table from
-- practice_attempts because it records something else entirely: a pass/fail
-- judgement with prose feedback over the text that was written, with no wpm and
-- no per-character accuracy to speak of. Splitting them also keeps every query
-- over practice_attempts meaning "typing speed" without a mode filter.
CREATE TABLE IF NOT EXISTS practice_recall_attempts (
    id TEXT PRIMARY KEY,
    snippet_id TEXT NOT NULL,
    submitted TEXT NOT NULL,
    verdict TEXT NOT NULL,
    passed INTEGER NOT NULL DEFAULT 0,
    feedback TEXT,
    -- 'model' when the grader read it, 'fallback' when llama-server was down
    -- and it fell back to a text comparison. Stored so a run of harsh verdicts
    -- can be traced to an offline grader rather than to the answers.
    graded_by TEXT NOT NULL DEFAULT 'model',
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_practice_recall_attempts_snippet ON practice_recall_attempts(snippet_id, created_at DESC);

CREATE TABLE IF NOT EXISTS email_accounts (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'gmail' CHECK(provider IN ('gmail','outlook','imap')),
    email_address TEXT NOT NULL,
    access_token TEXT,
    refresh_token TEXT,
    token_expires_at INTEGER,
    scope TEXT,
    history_id TEXT,
    -- IMAP-only (provider IN ('outlook','imap')): outlook's host/port are
    -- fixed constants (backend/email/outlook_client.py) but still stored so
    -- the row is self-describing; imap's are user-supplied. imap_password is
    -- plaintext, matching access_token/refresh_token above — this is a
    -- single-user local app with no at-rest secret encryption anywhere else.
    imap_host TEXT,
    imap_port INTEGER,
    imap_username TEXT,
    imap_password TEXT,
    -- IMAP sync cursor (INBOX only in v1 — see backend/email/imap_client.py).
    -- Gmail uses history_id above instead; these stay NULL for gmail rows.
    uid_validity INTEGER,
    uid_next INTEGER,
    last_synced_at INTEGER,
    last_sync_error TEXT,
    sync_enabled INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(provider, email_address)
);

-- category/job_status/classified_at stay NULL until the background classifier
-- runs; classified_at IS NULL is itself the "pending" state (see ai/email.py).
CREATE TABLE IF NOT EXISTS emails (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES email_accounts(id) ON DELETE CASCADE,
    -- The provider's own native message id: Gmail's message id, Outlook's
    -- IMAP UID, or a generic IMAP account's UID.
    provider_message_id TEXT NOT NULL,
    thread_id TEXT,
    subject TEXT,
    sender TEXT,
    sender_email TEXT,
    snippet TEXT,
    body_text TEXT NOT NULL DEFAULT '',
    -- Sanitized at import (backend/email/sanitize.py), rendered as trusted
    -- markup — the fanfic chapter contract. Empty for plain-text-only mail,
    -- which is why body_text stays the thing the classifier reads.
    body_html TEXT NOT NULL DEFAULT '',
    label_ids TEXT,
    received_at INTEGER NOT NULL,
    category TEXT CHECK(category IN ('job_application','newsletter','notification','personal','other')),
    job_status TEXT CHECK(job_status IN ('sent','rejection','interview_next_step','other_update')),
    classified_at INTEGER,
    classification_error TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(account_id, provider_message_id)
);

CREATE INDEX IF NOT EXISTS idx_emails_received ON emails(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_emails_category ON emails(category);
CREATE INDEX IF NOT EXISTS idx_emails_job_status ON emails(job_status) WHERE job_status IS NOT NULL;

-- One row per distinct image URL seen in email HTML. Keyed by a hash of the
-- URL rather than a ULID because backend/email/sanitize.py has to name the
-- local path at import time, before anything is fetched — the key must be
-- derivable from the URL alone.
--
-- The bytes live on disk under content_hash (backend/email/media.py), never
-- here: identical logos across thousands of messages converge on one file,
-- and a mail archive's images are what the external drive is for.
-- content_hash is NULL until fetched, and many rows share one — it is
-- deliberately not unique.
CREATE TABLE IF NOT EXISTS email_images (
    url_hash TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    content_hash TEXT,
    extension TEXT,
    content_type TEXT,
    byte_size INTEGER,
    -- pending -> stored | failed | skipped. 'skipped' is terminal and means
    -- the URL was refused (not public, wrong content type, too big); 'failed'
    -- is retryable and carries attempt_count so a dead CDN stops being asked.
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','stored','failed','skipped')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at INTEGER NOT NULL,
    fetched_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_email_images_status ON email_images(status, attempt_count);
CREATE INDEX IF NOT EXISTS idx_email_images_content ON email_images(content_hash) WHERE content_hash IS NOT NULL;
