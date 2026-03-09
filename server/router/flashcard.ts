import { z } from 'zod';
import { router, publicProcedure } from './trpc.js';
import { db, schema } from '../db/index.js';
import { eq, lte, desc, like } from 'drizzle-orm';
import { ulid } from 'ulid';
import { supermemo, SuperMemoItem, SuperMemoGrade } from 'supermemo';
import { generateFlashcardsFromContent, generateFlashcardsForTopic } from '../ai/flashcards.js';

export const flashcardRouter = router({
  // List all flashcards
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
        .from(schema.flashcards)
        .orderBy(desc(schema.flashcards.createdAt))
        .limit(limit)
        .offset(offset);
    }),

  // Get cards due for review
  getDue: publicProcedure.query(async () => {
    const now = new Date();
    return db
      .select()
      .from(schema.flashcards)
      .where(lte(schema.flashcards.nextReview, now))
      .orderBy(schema.flashcards.nextReview)
      .limit(20);
  }),

  // Get a single flashcard
  get: publicProcedure
    .input(z.object({ id: z.string() }))
    .query(async ({ input }) => {
      const [card] = await db
        .select()
        .from(schema.flashcards)
        .where(eq(schema.flashcards.id, input.id))
        .limit(1);
      return card || null;
    }),

  // Create a new flashcard
  create: publicProcedure
    .input(
      z.object({
        front: z.string(),
        back: z.string(),
        sourceId: z.string().optional(),
      })
    )
    .mutation(async ({ input }) => {
      const now = new Date();
      const id = ulid();

      await db.insert(schema.flashcards).values({
        id,
        front: input.front,
        back: input.back,
        sourceId: input.sourceId,
        easiness: 2.5,
        interval: 0,
        repetitions: 0,
        nextReview: now,
        createdAt: now,
      });

      return { id };
    }),

  // Review a flashcard (SM-2 algorithm)
  review: publicProcedure
    .input(
      z.object({
        id: z.string(),
        grade: z.number().min(0).max(5), // SM-2 grade: 0-5
      })
    )
    .mutation(async ({ input }) => {
      const [card] = await db
        .select()
        .from(schema.flashcards)
        .where(eq(schema.flashcards.id, input.id))
        .limit(1);

      if (!card) throw new Error('Flashcard not found');

      const item: SuperMemoItem = {
        interval: card.interval || 0,
        repetition: card.repetitions || 0,
        efactor: card.easiness || 2.5,
      };

      const result = supermemo(item, input.grade as SuperMemoGrade);

      const nextReview = new Date();
      nextReview.setDate(nextReview.getDate() + result.interval);

      await db
        .update(schema.flashcards)
        .set({
          easiness: result.efactor,
          interval: result.interval,
          repetitions: result.repetition,
          nextReview,
        })
        .where(eq(schema.flashcards.id, input.id));

      return { nextReview, interval: result.interval };
    }),

  // Update a flashcard
  update: publicProcedure
    .input(
      z.object({
        id: z.string(),
        front: z.string().optional(),
        back: z.string().optional(),
      })
    )
    .mutation(async ({ input }) => {
      const { id, ...updates } = input;
      const values: Record<string, unknown> = {};

      if (updates.front !== undefined) values.front = updates.front;
      if (updates.back !== undefined) values.back = updates.back;

      if (Object.keys(values).length > 0) {
        await db.update(schema.flashcards).set(values).where(eq(schema.flashcards.id, id));
      }
    }),

  // Delete a flashcard
  delete: publicProcedure
    .input(z.object({ id: z.string() }))
    .mutation(async ({ input }) => {
      await db.delete(schema.flashcards).where(eq(schema.flashcards.id, input.id));
    }),

  // Generate flashcards from a journal entry
  generateFromJournal: publicProcedure
    .input(z.object({ journalId: z.string() }))
    .mutation(async ({ input }) => {
      // Get the journal entry
      const [journal] = await db
        .select()
        .from(schema.journalEntries)
        .where(eq(schema.journalEntries.id, input.journalId))
        .limit(1);

      if (!journal) throw new Error('Journal entry not found');

      // Generate flashcards using AI
      const generated = await generateFlashcardsFromContent(
        journal.content,
        journal.title || undefined
      );

      // Save the flashcards
      const now = new Date();
      const ids: string[] = [];

      for (const card of generated) {
        const id = ulid();
        ids.push(id);

        await db.insert(schema.flashcards).values({
          id,
          front: card.front,
          back: card.back,
          sourceId: input.journalId,
          easiness: 2.5,
          interval: 0,
          repetitions: 0,
          nextReview: now,
          createdAt: now,
        });
      }

      return { count: ids.length, ids };
    }),

  // Generate flashcards for a topic (for "quiz me" command)
  generateForTopic: publicProcedure
    .input(z.object({ topic: z.string() }))
    .mutation(async ({ input }) => {
      // Search for related journal entries to provide context
      const relatedEntries = await db
        .select()
        .from(schema.journalEntries)
        .where(like(schema.journalEntries.content, `%${input.topic}%`))
        .limit(3);

      const context = relatedEntries.length > 0
        ? relatedEntries.map((e) => e.content).join('\n\n---\n\n')
        : undefined;

      // Generate flashcards using AI
      const generated = await generateFlashcardsForTopic(input.topic, context);

      // Save the flashcards
      const now = new Date();
      const ids: string[] = [];

      for (const card of generated) {
        const id = ulid();
        ids.push(id);

        await db.insert(schema.flashcards).values({
          id,
          front: card.front,
          back: card.back,
          sourceId: null,
          easiness: 2.5,
          interval: 0,
          repetitions: 0,
          nextReview: now,
          createdAt: now,
        });
      }

      return { count: ids.length, ids };
    }),

  // Get flashcards by source (journal entry)
  getBySource: publicProcedure
    .input(z.object({ sourceId: z.string() }))
    .query(async ({ input }) => {
      return db
        .select()
        .from(schema.flashcards)
        .where(eq(schema.flashcards.sourceId, input.sourceId))
        .orderBy(desc(schema.flashcards.createdAt));
    }),

  // Get stats
  getStats: publicProcedure.query(async () => {
    const now = new Date();
    const allCards = await db.select().from(schema.flashcards);

    const total = allCards.length;
    const due = allCards.filter((c) => c.nextReview <= now).length;
    const mastered = allCards.filter((c) => (c.interval || 0) >= 21).length; // 21+ days interval = mastered
    const learning = total - mastered;

    return { total, due, mastered, learning };
  }),
});
