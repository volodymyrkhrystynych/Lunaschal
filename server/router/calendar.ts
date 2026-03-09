import { z } from 'zod';
import { router, publicProcedure } from './trpc.js';
import { db, schema } from '../db/index.js';
import { eq, desc, between, and, inArray } from 'drizzle-orm';
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
        .where(between(schema.calendarEvents.date, input.startDate, input.endDate))
        .orderBy(schema.calendarEvents.date, schema.calendarEvents.time);
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

  // Get a single event with linked journal entries
  get: publicProcedure
    .input(z.object({ id: z.string() }))
    .query(async ({ input }) => {
      const [event] = await db
        .select()
        .from(schema.calendarEvents)
        .where(eq(schema.calendarEvents.id, input.id))
        .limit(1);

      if (!event) return null;

      // Get linked journal entries
      const links = await db
        .select()
        .from(schema.calendarJournalLinks)
        .where(eq(schema.calendarJournalLinks.calendarEventId, input.id));

      let linkedJournals: typeof schema.journalEntries.$inferSelect[] = [];
      if (links.length > 0) {
        linkedJournals = await db
          .select()
          .from(schema.journalEntries)
          .where(
            inArray(
              schema.journalEntries.id,
              links.map((l) => l.journalEntryId)
            )
          );
      }

      // Also check direct journal link
      if (event.journalId) {
        const [directJournal] = await db
          .select()
          .from(schema.journalEntries)
          .where(eq(schema.journalEntries.id, event.journalId))
          .limit(1);

        if (directJournal && !linkedJournals.find((j) => j.id === directJournal.id)) {
          linkedJournals.unshift(directJournal);
        }
      }

      return { ...event, linkedJournals };
    }),

  // Create a new event
  create: publicProcedure
    .input(
      z.object({
        title: z.string(),
        description: z.string().optional(),
        date: z.string(), // YYYY-MM-DD
        time: z.string().optional(), // HH:MM
        endTime: z.string().optional(), // HH:MM
        tags: z.array(z.string()).optional(),
        journalId: z.string().optional(), // Direct link to journal entry
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
        endTime: input.endTime,
        tags: input.tags ? JSON.stringify(input.tags) : null,
        journalId: input.journalId,
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
        endTime: z.string().optional(),
        tags: z.array(z.string()).optional(),
        journalId: z.string().nullable().optional(),
      })
    )
    .mutation(async ({ input }) => {
      const { id, ...updates } = input;
      const values: Record<string, unknown> = {};

      if (updates.title !== undefined) values.title = updates.title;
      if (updates.description !== undefined) values.description = updates.description;
      if (updates.date !== undefined) values.date = updates.date;
      if (updates.time !== undefined) values.time = updates.time;
      if (updates.endTime !== undefined) values.endTime = updates.endTime;
      if (updates.tags !== undefined) values.tags = JSON.stringify(updates.tags);
      if (updates.journalId !== undefined) values.journalId = updates.journalId;

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

  // Link a journal entry to a calendar event
  linkJournal: publicProcedure
    .input(
      z.object({
        calendarEventId: z.string(),
        journalEntryId: z.string(),
      })
    )
    .mutation(async ({ input }) => {
      const id = ulid();
      const now = new Date();

      // Check if link already exists
      const [existing] = await db
        .select()
        .from(schema.calendarJournalLinks)
        .where(
          and(
            eq(schema.calendarJournalLinks.calendarEventId, input.calendarEventId),
            eq(schema.calendarJournalLinks.journalEntryId, input.journalEntryId)
          )
        )
        .limit(1);

      if (existing) return { id: existing.id };

      await db.insert(schema.calendarJournalLinks).values({
        id,
        calendarEventId: input.calendarEventId,
        journalEntryId: input.journalEntryId,
        createdAt: now,
      });

      return { id };
    }),

  // Unlink a journal entry from a calendar event
  unlinkJournal: publicProcedure
    .input(
      z.object({
        calendarEventId: z.string(),
        journalEntryId: z.string(),
      })
    )
    .mutation(async ({ input }) => {
      await db
        .delete(schema.calendarJournalLinks)
        .where(
          and(
            eq(schema.calendarJournalLinks.calendarEventId, input.calendarEventId),
            eq(schema.calendarJournalLinks.journalEntryId, input.journalEntryId)
          )
        );
    }),

  // Get events for a specific week
  listByWeek: publicProcedure
    .input(z.object({ date: z.string() })) // Any date in the week
    .query(async ({ input }) => {
      const date = new Date(input.date);
      const dayOfWeek = date.getDay();
      const startOfWeek = new Date(date);
      startOfWeek.setDate(date.getDate() - dayOfWeek);
      const endOfWeek = new Date(startOfWeek);
      endOfWeek.setDate(startOfWeek.getDate() + 6);

      const startDate = startOfWeek.toISOString().split('T')[0];
      const endDate = endOfWeek.toISOString().split('T')[0];

      return db
        .select()
        .from(schema.calendarEvents)
        .where(between(schema.calendarEvents.date, startDate, endDate))
        .orderBy(schema.calendarEvents.date, schema.calendarEvents.time);
    }),

  // Find related journal entries for a date (entries created on or mentioning that date)
  findRelatedJournals: publicProcedure
    .input(z.object({ date: z.string() }))
    .query(async ({ input }) => {
      // Get journals created on that date
      const startOfDay = new Date(input.date + 'T00:00:00');
      const endOfDay = new Date(input.date + 'T23:59:59');

      return db
        .select()
        .from(schema.journalEntries)
        .where(between(schema.journalEntries.createdAt, startOfDay, endOfDay))
        .orderBy(desc(schema.journalEntries.createdAt));
    }),
});
