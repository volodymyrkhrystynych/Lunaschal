import Database from 'better-sqlite3';

// Initialize FTS5 tables for full-text search
export function initFTS(sqlite: Database.Database) {
  // Create FTS5 virtual table for journal entries
  sqlite.exec(`
    CREATE VIRTUAL TABLE IF NOT EXISTS journal_fts USING fts5(
      id UNINDEXED,
      title,
      content,
      tags,
      content='journal_entries',
      content_rowid='rowid'
    );
  `);

  // Create triggers to keep FTS in sync
  sqlite.exec(`
    CREATE TRIGGER IF NOT EXISTS journal_ai AFTER INSERT ON journal_entries BEGIN
      INSERT INTO journal_fts(rowid, id, title, content, tags)
      VALUES (NEW.rowid, NEW.id, NEW.title, NEW.content, NEW.tags);
    END;
  `);

  sqlite.exec(`
    CREATE TRIGGER IF NOT EXISTS journal_ad AFTER DELETE ON journal_entries BEGIN
      INSERT INTO journal_fts(journal_fts, rowid, id, title, content, tags)
      VALUES ('delete', OLD.rowid, OLD.id, OLD.title, OLD.content, OLD.tags);
    END;
  `);

  sqlite.exec(`
    CREATE TRIGGER IF NOT EXISTS journal_au AFTER UPDATE ON journal_entries BEGIN
      INSERT INTO journal_fts(journal_fts, rowid, id, title, content, tags)
      VALUES ('delete', OLD.rowid, OLD.id, OLD.title, OLD.content, OLD.tags);
      INSERT INTO journal_fts(rowid, id, title, content, tags)
      VALUES (NEW.rowid, NEW.id, NEW.title, NEW.content, NEW.tags);
    END;
  `);

  // Rebuild FTS index from existing data
  rebuildFTSIndex(sqlite);
}

// Rebuild FTS index (useful after manual data changes)
export function rebuildFTSIndex(sqlite: Database.Database) {
  sqlite.exec(`
    INSERT INTO journal_fts(journal_fts) VALUES('rebuild');
  `);
}

// Search journal entries using FTS5
export function searchJournalFTS(
  sqlite: Database.Database,
  query: string,
  limit: number = 50
): Array<{ id: string; rank: number }> {
  // Escape special FTS5 characters and prepare query
  const escapedQuery = query
    .replace(/['"]/g, '')
    .split(/\s+/)
    .filter((word) => word.length > 0)
    .map((word) => `"${word}"*`)
    .join(' OR ');

  if (!escapedQuery) return [];

  const stmt = sqlite.prepare(`
    SELECT id, rank
    FROM journal_fts
    WHERE journal_fts MATCH ?
    ORDER BY rank
    LIMIT ?
  `);

  return stmt.all(escapedQuery, limit) as Array<{ id: string; rank: number }>;
}
