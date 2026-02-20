import { z } from 'zod';
import { router, publicProcedure } from './trpc.js';
import { db, schema } from '../db/index.js';
import { eq, desc, between, and } from 'drizzle-orm';
import { ulid } from 'ulid';

export const calendarRouter = router({
  // List events for a date range
  listByRange: publicProcedure
    .input(
      z.object({
        startDate: z.string(), // YYYY-MM-DD
        endDate: z.string(), // YYYY-MM-DD
      })
    )
    .query(async ({ input }) => {
      return db
        .select()
        .from(schema.calendarEvents)
        .where(
          and(
            between(schema.calendarEvents.date, input.startDate, input.endDate)
          )
        )
        .orderBy(schema.calendarEvents.date);
    }),

  // List events for a specific date
  listByDate: publicProcedure
    .input(z.object({ date: z.string() }))
    .query(async ({ input }) => {
      return db
        .select()
        .from(schema.calendarEvents)
        .where(eq(schema.calendarEvents.date, input.date))
        .orderBy(schema.calendarEvents.time);
    }),

  // Get a single event
  get: publicProcedure
    .input(z.object({ id: z.string() }))
    .query(async ({ input }) => {
      const [event] = await db
        .select()
        .from(schema.calendarEvents)
        .where(eq(schema.calendarEvents.id, input.id))
        .limit(1);
      return event || null;
    }),

  // Create a new event
  create: publicProcedure
    .input(
      z.object({
        title: z.string(),
        description: z.string().optional(),
        date: z.string(), // YYYY-MM-DD
        time: z.string().optional(), // HH:MM
        tags: z.array(z.string()).optional(),
      })
    )
    .mutation(async ({ input }) => {
      const now = new Date();
      const id = ulid();

      await db.insert(schema.calendarEvents).values({
        id,
        title: input.title,
        description: input.description,
        date: input.date,
        time: input.time,
        tags: input.tags ? JSON.stringify(input.tags) : null,
        createdAt: now,
      });

      return { id };
    }),

  // Update an event
  update: publicProcedure
    .input(
      z.object({
        id: z.string(),
        title: z.string().optional(),
        description: z.string().optional(),
        date: z.string().optional(),
        time: z.string().optional(),
        tags: z.array(z.string()).optional(),
      })
    )
    .mutation(async ({ input }) => {
      const { id, ...updates } = input;
      const values: Record<string, unknown> = {};

      if (updates.title !== undefined) values.title = updates.title;
      if (updates.description !== undefined) values.description = updates.description;
      if (updates.date !== undefined) values.date = updates.date;
      if (updates.time !== undefined) values.time = updates.time;
      if (updates.tags !== undefined) values.tags = JSON.stringify(updates.tags);

      if (Object.keys(values).length > 0) {
        await db.update(schema.calendarEvents).set(values).where(eq(schema.calendarEvents.id, id));
      }
    }),

  // Delete an event
  delete: publicProcedure
    .input(z.object({ id: z.string() }))
    .mutation(async ({ input }) => {
      await db.delete(schema.calendarEvents).where(eq(schema.calendarEvents.id, input.id));
    }),
});
