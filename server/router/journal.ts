import { z } from 'zod';
import { router, publicProcedure } from './trpc.js';
import { db, schema, searchJournal } from '../db/index.js';
import { eq, desc, inArray } from 'drizzle-orm';
import { ulid } from 'ulid';
import { syncJournalEmbeddings, deleteJournalEmbeddings, searchForContext } from '../ai/rag.js';
import { isEmbeddingsConfigured } from '../ai/embeddings.js';

export const journalRouter = router({
  // List journal entries
  list: publicProcedure
    .input(
      z.object({
        limit: z.number().min(1).max(100).default(50),
        offset: z.number().min(0).default(0),
      }).optional()
    )
    .query(async ({ input }) => {
      const { limit = 50, offset = 0 } = input || {};
      return db
        .select()
        .from(schema.journalEntries)
        .orderBy(desc(schema.journalEntries.createdAt))
        .limit(limit)
        .offset(offset);
    }),

  // Get a single entry
  get: publicProcedure
    .input(z.object({ id: z.string() }))
    .query(async ({ input }) => {
      const [entry] = await db
        .select()
        .from(schema.journalEntries)
        .where(eq(schema.journalEntries.id, input.id))
        .limit(1);
      return entry || null;
    }),

  // Create a new entry
  create: publicProcedure
    .input(
      z.object({
        content: z.string(),
        title: z.string().optional(),
        tags: z.array(z.string()).optional(),
      })
    )
    .mutation(async ({ input }) => {
      const now = new Date();
      const id = ulid();

      await db.insert(schema.journalEntries).values({
        id,
        content: input.content,
        title: input.title,
        tags: input.tags ? JSON.stringify(input.tags) : null,
        createdAt: now,
        updatedAt: now,
      });

      // Sync embeddings in background (don't block response)
      isEmbeddingsConfigured().then((configured) => {
        if (configured) {
          syncJournalEmbeddings(id).catch((err) => {
            console.error('Failed to sync embeddings for journal:', err);
          });
        }
      });

      return { id };
    }),

  // Update an entry
  update: publicProcedure
    .input(
      z.object({
        id: z.string(),
        content: z.string().optional(),
        title: z.string().optional(),
        tags: z.array(z.string()).optional(),
      })
    )
    .mutation(async ({ input }) => {
      const { id, ...updates } = input;
      const values: Record<string, unknown> = { updatedAt: new Date() };

      if (updates.content !== undefined) values.content = updates.content;
      if (updates.title !== undefined) values.title = updates.title;
      if (updates.tags !== undefined) values.tags = JSON.stringify(updates.tags);

      await db.update(schema.journalEntries).set(values).where(eq(schema.journalEntries.id, id));

      // Re-sync embeddings if content changed
      if (updates.content !== undefined || updates.title !== undefined) {
        isEmbeddingsConfigured().then((configured) => {
          if (configured) {
            syncJournalEmbeddings(id).catch((err) => {
              console.error('Failed to sync embeddings for journal:', err);
            });
          }
        });
      }
    }),

  // Delete an entry
  delete: publicProcedure
    .input(z.object({ id: z.string() }))
    .mutation(async ({ input }) => {
      // Delete embeddings first
      deleteJournalEmbeddings(input.id);

      await db.delete(schema.journalEntries).where(eq(schema.journalEntries.id, input.id));
    }),

  // Full-text search using FTS5
  search: publicProcedure
    .input(z.object({ query: z.string(), limit: z.number().min(1).max(100).default(50) }))
    .query(async ({ input }) => {
      if (!input.query.trim()) {
        return [];
      }

      // Get matching IDs from FTS5
      const ftsResults = searchJournal(input.query, input.limit);

      if (ftsResults.length === 0) {
        return [];
      }

      // Fetch full entries, maintaining FTS rank order
      const ids = ftsResults.map((r) => r.id);
      const entries = await db
        .select()
        .from(schema.journalEntries)
        .where(inArray(schema.journalEntries.id, ids));

      // Sort by FTS rank
      const idToRank = new Map(ftsResults.map((r) => [r.id, r.rank]));
      return entries.sort((a, b) => (idToRank.get(a.id) || 0) - (idToRank.get(b.id) || 0));
    }),

  // Semantic search using embeddings
  semanticSearch: publicProcedure
    .input(z.object({ query: z.string(), limit: z.number().min(1).max(20).default(5) }))
    .query(async ({ input }) => {
      if (!input.query.trim()) {
        return [];
      }

      const configured = await isEmbeddingsConfigured();
      if (!configured) {
        return [];
      }

      const results = await searchForContext(input.query, input.limit);
      return results;
    }),
});
