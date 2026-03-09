import Database from 'better-sqlite3';
import { drizzle } from 'drizzle-orm/better-sqlite3';
import { migrate } from 'drizzle-orm/better-sqlite3/migrator';
import * as schema from './schema.js';
import { existsSync, mkdirSync } from 'fs';
import { dirname } from 'path';
import { initFTS, searchJournalFTS } from './fts.js';
import {
  initVectorStore,
  insertEmbedding,
  deleteEmbeddingsBySource,
  searchSimilar,
  type VectorSearchResult,
} from './vectors.js';

const DB_PATH = process.env.DATABASE_URL || './data/lunaschal.db';

// Ensure data directory exists
const dbDir = dirname(DB_PATH);
if (!existsSync(dbDir)) {
  mkdirSync(dbDir, { recursive: true });
}

const sqlite = new Database(DB_PATH);
sqlite.pragma('journal_mode = WAL');
sqlite.pragma('foreign_keys = ON');

export const db = drizzle(sqlite, { schema });

// Run migrations and initialize FTS + Vector store
export function runMigrations() {
  migrate(db, { migrationsFolder: './server/db/migrations' });
  // Initialize FTS5 after migrations
  initFTS(sqlite);
  // Initialize vector store for RAG
  try {
    initVectorStore(sqlite);
  } catch (error) {
    console.warn('Vector store initialization skipped (sqlite-vec may not be available):', error);
  }
}

// Export FTS search function
export function searchJournal(query: string, limit?: number) {
  return searchJournalFTS(sqlite, query, limit);
}

// Vector store functions
export function addEmbedding(
  id: string,
  embedding: number[],
  metadata: {
    sourceType: 'journal' | 'flashcard' | 'message';
    sourceId: string;
    chunkIndex: number;
    chunkText: string;
  }
) {
  insertEmbedding(sqlite, id, embedding, metadata);
}

export function removeEmbeddingsBySource(sourceType: string, sourceId: string) {
  deleteEmbeddingsBySource(sqlite, sourceType, sourceId);
}

export function searchEmbeddings(
  queryEmbedding: number[],
  limit?: number,
  sourceType?: string
): VectorSearchResult[] {
  return searchSimilar(sqlite, queryEmbedding, limit, sourceType);
}

export { schema };
export type { VectorSearchResult };
