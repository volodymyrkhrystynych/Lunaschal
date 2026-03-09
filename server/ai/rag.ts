import { ulid } from 'ulid';
import { eq } from 'drizzle-orm';
import { db, schema, addEmbedding, removeEmbeddingsBySource, searchEmbeddings } from '../db/index.js';
import { generateEmbeddings, generateEmbedding, isEmbeddingsConfigured } from './embeddings.js';

// Sync a single journal entry to embeddings
export async function syncJournalEmbeddings(journalId: string): Promise<number> {
  const configured = await isEmbeddingsConfigured();
  if (!configured) {
    console.warn('Embeddings not configured, skipping sync');
    return 0;
  }

  // Get the journal entry
  const [journal] = await db
    .select()
    .from(schema.journalEntries)
    .where(eq(schema.journalEntries.id, journalId))
    .limit(1);

  if (!journal) {
    throw new Error('Journal entry not found');
  }

  // Remove old embeddings for this entry
  removeEmbeddingsBySource('journal', journalId);

  // Generate new embeddings
  const content = journal.title ? `${journal.title}\n\n${journal.content}` : journal.content;
  const results = await generateEmbeddings(content);

  // Store embeddings
  for (const result of results) {
    const id = ulid();
    addEmbedding(id, result.embedding, {
      sourceType: 'journal',
      sourceId: journalId,
      chunkIndex: result.chunkIndex,
      chunkText: result.chunkText,
    });
  }

  return results.length;
}

// Sync all journal entries (for initial setup or rebuild)
export async function syncAllJournalEmbeddings(
  onProgress?: (current: number, total: number) => void
): Promise<{ synced: number; chunks: number }> {
  const configured = await isEmbeddingsConfigured();
  if (!configured) {
    throw new Error('Embeddings not configured');
  }

  const journals = await db.select().from(schema.journalEntries);
  let totalChunks = 0;

  for (let i = 0; i < journals.length; i++) {
    const journal = journals[i];
    try {
      const chunks = await syncJournalEmbeddings(journal.id);
      totalChunks += chunks;
      onProgress?.(i + 1, journals.length);
    } catch (error) {
      console.error(`Failed to sync journal ${journal.id}:`, error);
    }
  }

  return { synced: journals.length, chunks: totalChunks };
}

// Delete embeddings when a journal entry is deleted
export function deleteJournalEmbeddings(journalId: string): void {
  removeEmbeddingsBySource('journal', journalId);
}

// Semantic search for relevant context
export interface RAGSearchResult {
  sourceType: string;
  sourceId: string;
  content: string;
  score: number;
  metadata?: {
    title?: string;
    createdAt?: Date;
  };
}

export async function searchForContext(
  query: string,
  limit: number = 5
): Promise<RAGSearchResult[]> {
  const configured = await isEmbeddingsConfigured();
  if (!configured) {
    return [];
  }

  // Generate embedding for the query
  const queryEmbedding = await generateEmbedding(query);

  // Search for similar embeddings
  const results = searchEmbeddings(queryEmbedding, limit * 2); // Get more to dedupe

  // Deduplicate by source and get full content
  const sourceMap = new Map<string, RAGSearchResult>();

  for (const result of results) {
    const key = `${result.sourceType}:${result.sourceId}`;
    if (sourceMap.has(key)) continue;

    // Get full source content
    if (result.sourceType === 'journal') {
      const [journal] = await db
        .select()
        .from(schema.journalEntries)
        .where(eq(schema.journalEntries.id, result.sourceId))
        .limit(1);

      if (journal) {
        sourceMap.set(key, {
          sourceType: result.sourceType,
          sourceId: result.sourceId,
          content: journal.content,
          score: 1 - result.distance, // Convert distance to similarity score
          metadata: {
            title: journal.title || undefined,
            createdAt: journal.createdAt,
          },
        });
      }
    }

    if (sourceMap.size >= limit) break;
  }

  return Array.from(sourceMap.values());
}

// Format RAG results as context for the AI
export function formatRAGContext(results: RAGSearchResult[]): string {
  if (results.length === 0) {
    return '';
  }

  const sections = results.map((result, index) => {
    const header = result.metadata?.title
      ? `[${result.metadata.title}]`
      : `[Entry from ${result.metadata?.createdAt?.toLocaleDateString() || 'unknown date'}]`;

    return `--- Context ${index + 1} ${header} ---\n${result.content}`;
  });

  return `Here is relevant information from the user's personal knowledge base:\n\n${sections.join('\n\n')}`;
}

// Check if embeddings exist for a source
export async function hasEmbeddings(sourceType: string, sourceId: string): Promise<boolean> {
  // This is a simple check - in production you'd query the metadata table
  try {
    const results = searchEmbeddings(new Array(1536).fill(0), 1, sourceType);
    return results.some((r) => r.sourceId === sourceId);
  } catch {
    return false;
  }
}

// Get embedding stats
export interface EmbeddingStats {
  totalJournals: number;
  indexedJournals: number;
  totalChunks: number;
  isConfigured: boolean;
}

export async function getEmbeddingStats(): Promise<EmbeddingStats> {
  const configured = await isEmbeddingsConfigured();
  const totalJournals = (await db.select().from(schema.journalEntries)).length;

  if (!configured) {
    return {
      totalJournals,
      indexedJournals: 0,
      totalChunks: 0,
      isConfigured: false,
    };
  }

  // Count unique indexed journals - we'll estimate from chunks
  // In production, query the metadata table directly
  return {
    totalJournals,
    indexedJournals: 0, // Would need metadata query
    totalChunks: 0, // Would need metadata query
    isConfigured: true,
  };
}
