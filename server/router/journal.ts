import { z } from 'zod';
import { router, publicProcedure } from './trpc.js';
import { db, schema } from '../db/index.js';
import { eq, desc, like } from 'drizzle-orm';
import { ulid } from 'ulid';

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
    }),

  // Delete an entry
  delete: publicProcedure
    .input(z.object({ id: z.string() }))
    .mutation(async ({ input }) => {
      await db.delete(schema.journalEntries).where(eq(schema.journalEntries.id, input.id));
    }),

  // Simple search (will be enhanced with FTS5 later)
  search: publicProcedure
    .input(z.object({ query: z.string() }))
    .query(async ({ input }) => {
      return db
        .select()
        .from(schema.journalEntries)
        .where(like(schema.journalEntries.content, `%${input.query}%`))
        .orderBy(desc(schema.journalEntries.createdAt))
        .limit(50);
    }),
});
