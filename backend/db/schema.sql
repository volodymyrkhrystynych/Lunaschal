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
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_journal_attachments_entry
    ON journal_attachments(entry_id, position);

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
    split_from TEXT REFERENCES calendar_events(id) ON DELETE SET NULL
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
    created_at INTEGER NOT NULL
);

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

-- --- Lifestyle tab: workouts, body weight, selfies, calories -----------------
-- See docs/lifestyle-tab.md. Chores are deliberately absent: they already exist
-- as todos with list='chores' (backend/todo_recurrence.py) and the Lifestyle tab
-- renders that same list rather than forking a second one.

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
