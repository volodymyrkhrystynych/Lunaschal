import Database from 'better-sqlite3';
import * as sqliteVec from 'sqlite-vec';

// Embedding dimensions for different providers
const EMBEDDING_DIMENSIONS: Record<string, number> = {
  'text-embedding-3-small': 1536,
  'text-embedding-3-large': 3072,
  'text-embedding-ada-002': 1536,
  'embedding-001': 768, // Google
  default: 1536,
};

export function initVectorStore(sqlite: Database.Database, dimensions: number = 1536) {
  // Load sqlite-vec extension
  sqliteVec.load(sqlite);

  // Create virtual table for vector search
  sqlite.exec(`
    CREATE VIRTUAL TABLE IF NOT EXISTS vec_embeddings USING vec0(
      id TEXT PRIMARY KEY,
      embedding FLOAT[${dimensions}]
    );
  `);

  // Create metadata table for embedding info
  sqlite.exec(`
    CREATE TABLE IF NOT EXISTS embedding_metadata (
      id TEXT PRIMARY KEY,
      source_type TEXT NOT NULL,
      source_id TEXT NOT NULL,
      chunk_index INTEGER NOT NULL DEFAULT 0,
      chunk_text TEXT NOT NULL,
      created_at INTEGER NOT NULL
    );
  `);

  // Create index for source lookups
  sqlite.exec(`
    CREATE INDEX IF NOT EXISTS idx_embedding_source
    ON embedding_metadata(source_type, source_id);
  `);
}

export function insertEmbedding(
  sqlite: Database.Database,
  id: string,
  embedding: number[],
  metadata: {
    sourceType: 'journal' | 'flashcard' | 'message';
    sourceId: string;
    chunkIndex: number;
    chunkText: string;
  }
) {
  const now = Date.now();

  // Insert into vector table
  const vecStmt = sqlite.prepare(`
    INSERT OR REPLACE INTO vec_embeddings(id, embedding)
    VALUES (?, ?)
  `);
  vecStmt.run(id, new Float32Array(embedding));

  // Insert metadata
  const metaStmt = sqlite.prepare(`
    INSERT OR REPLACE INTO embedding_metadata(id, source_type, source_id, chunk_index, chunk_text, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
  `);
  metaStmt.run(id, metadata.sourceType, metadata.sourceId, metadata.chunkIndex, metadata.chunkText, now);
}

export function deleteEmbeddingsBySource(
  sqlite: Database.Database,
  sourceType: string,
  sourceId: string
) {
  // Get embedding IDs for this source
  const ids = sqlite
    .prepare(`SELECT id FROM embedding_metadata WHERE source_type = ? AND source_id = ?`)
    .all(sourceType, sourceId) as { id: string }[];

  if (ids.length === 0) return;

  // Delete from both tables
  const idList = ids.map((r) => r.id);
  for (const id of idList) {
    sqlite.prepare(`DELETE FROM vec_embeddings WHERE id = ?`).run(id);
    sqlite.prepare(`DELETE FROM embedding_metadata WHERE id = ?`).run(id);
  }
}

export interface VectorSearchResult {
  id: string;
  distance: number;
  sourceType: string;
  sourceId: string;
  chunkIndex: number;
  chunkText: string;
}

export function searchSimilar(
  sqlite: Database.Database,
  queryEmbedding: number[],
  limit: number = 5,
  sourceType?: string
): VectorSearchResult[] {
  let query = `
    SELECT
      v.id,
      v.distance,
      m.source_type,
      m.source_id,
      m.chunk_index,
      m.chunk_text
    FROM vec_embeddings v
    JOIN embedding_metadata m ON v.id = m.id
    WHERE v.embedding MATCH ?
  `;

  const params: (Float32Array | string | number)[] = [new Float32Array(queryEmbedding)];

  if (sourceType) {
    query += ` AND m.source_type = ?`;
    params.push(sourceType);
  }

  query += `
    ORDER BY v.distance
    LIMIT ?
  `;
  params.push(limit);

  const stmt = sqlite.prepare(query);
  return stmt.all(...params) as VectorSearchResult[];
}

export function getEmbeddingDimensions(model: string): number {
  return EMBEDDING_DIMENSIONS[model] || EMBEDDING_DIMENSIONS.default;
}
