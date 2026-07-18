CREATE TABLE IF NOT EXISTS journal_entries (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    raw_content TEXT,
    title TEXT,
    tags TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    date TEXT NOT NULL,
    time TEXT,
    end_time TEXT,
    tags TEXT,
    journal_id TEXT REFERENCES journal_entries(id),
    created_at INTEGER NOT NULL
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
    ollama_url TEXT DEFAULT 'http://localhost:11434',
    ollama_model TEXT,
    network_code TEXT,
    stt_backend TEXT,
    tts_backend TEXT,
    whisper_model TEXT,
    stt_device TEXT,
    voice_pipeline_enabled INTEGER DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS embedding_metadata (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    chunk_text TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_journal_created ON journal_entries(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_calendar_date ON calendar_events(date);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_embedding_source ON embedding_metadata(source_type, source_id);

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
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

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
