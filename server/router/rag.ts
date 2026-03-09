import { z } from 'zod';
import { router, publicProcedure } from './trpc.js';
import {
  syncJournalEmbeddings,
  syncAllJournalEmbeddings,
  searchForContext,
  getEmbeddingStats,
} from '../ai/rag.js';
import { isEmbeddingsConfigured } from '../ai/embeddings.js';

export const ragRouter = router({
  // Check if embeddings are configured
  isConfigured: publicProcedure.query(async () => {
    return isEmbeddingsConfigured();
  }),

  // Get embedding stats
  getStats: publicProcedure.query(async () => {
    return getEmbeddingStats();
  }),

  // Sync a single journal entry
  syncJournal: publicProcedure
    .input(z.object({ journalId: z.string() }))
    .mutation(async ({ input }) => {
      const chunks = await syncJournalEmbeddings(input.journalId);
      return { chunks };
    }),

  // Sync all journal entries
  syncAll: publicProcedure.mutation(async () => {
    const result = await syncAllJournalEmbeddings();
    return result;
  }),

  // Semantic search
  search: publicProcedure
    .input(
      z.object({
        query: z.string(),
        limit: z.number().min(1).max(20).default(5),
      })
    )
    .query(async ({ input }) => {
      const results = await searchForContext(input.query, input.limit);
      return results;
    }),
});
