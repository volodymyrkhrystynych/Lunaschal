CREATE TABLE IF NOT EXISTS journal_entries (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
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

CREATE TABLE IF NOT EXISTS flashcards (
    id TEXT PRIMARY KEY,
    front TEXT NOT NULL,
    back TEXT NOT NULL,
    source_id TEXT REFERENCES journal_entries(id),
    easiness REAL DEFAULT 2.5,
    interval INTEGER DEFAULT 0,
    repetitions INTEGER DEFAULT 0,
    next_review INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

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
CREATE INDEX IF NOT EXISTS idx_flashcard_next_review ON flashcards(next_review);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_embedding_source ON embedding_metadata(source_type, source_id);
