import { z } from 'zod';
import { router, publicProcedure } from './trpc.js';
import { db, schema } from '../db/index.js';
import { eq, desc } from 'drizzle-orm';
import { ulid } from 'ulid';
import { classifyIntent, shouldClassify } from '../ai/classifier.js';
import { searchForContext, formatRAGContext } from '../ai/rag.js';
import { isEmbeddingsConfigured } from '../ai/embeddings.js';

export const chatRouter = router({
  // List all conversations
  listConversations: publicProcedure.query(async () => {
    return db
      .select()
      .from(schema.conversations)
      .orderBy(desc(schema.conversations.updatedAt));
  }),

  // Get a single conversation with messages
  getConversation: publicProcedure
    .input(z.object({ id: z.string() }))
    .query(async ({ input }) => {
      const [conversation] = await db
        .select()
        .from(schema.conversations)
        .where(eq(schema.conversations.id, input.id))
        .limit(1);

      if (!conversation) return null;

      const messages = await db
        .select()
        .from(schema.messages)
        .where(eq(schema.messages.conversationId, input.id))
        .orderBy(schema.messages.createdAt);

      return { ...conversation, messages };
    }),

  // Create a new conversation
  createConversation: publicProcedure
    .input(z.object({ title: z.string().optional() }))
    .mutation(async ({ input }) => {
      const now = new Date();
      const id = ulid();

      await db.insert(schema.conversations).values({
        id,
        title: input.title || 'New Conversation',
        createdAt: now,
        updatedAt: now,
      });

      return { id };
    }),

  // Add a message to a conversation
  addMessage: publicProcedure
    .input(
      z.object({
        conversationId: z.string(),
        role: z.enum(['user', 'assistant', 'system']),
        content: z.string(),
        metadata: z.string().optional(),
      })
    )
    .mutation(async ({ input }) => {
      const now = new Date();
      const id = ulid();

      await db.insert(schema.messages).values({
        id,
        conversationId: input.conversationId,
        role: input.role,
        content: input.content,
        metadata: input.metadata,
        createdAt: now,
      });

      // Update conversation timestamp
      await db
        .update(schema.conversations)
        .set({ updatedAt: now })
        .where(eq(schema.conversations.id, input.conversationId));

      return { id };
    }),

  // Update conversation title
  updateConversationTitle: publicProcedure
    .input(z.object({ id: z.string(), title: z.string() }))
    .mutation(async ({ input }) => {
      await db
        .update(schema.conversations)
        .set({ title: input.title, updatedAt: new Date() })
        .where(eq(schema.conversations.id, input.id));
    }),

  // Delete a conversation
  deleteConversation: publicProcedure
    .input(z.object({ id: z.string() }))
    .mutation(async ({ input }) => {
      await db.delete(schema.conversations).where(eq(schema.conversations.id, input.id));
    }),

  // Classify a message's intent
  classifyMessage: publicProcedure
    .input(z.object({ message: z.string() }))
    .mutation(async ({ input }) => {
      // Quick check to avoid unnecessary API calls
      if (!shouldClassify(input.message)) {
        return { intent: 'conversation' as const, confidence: 1.0 };
      }
      return classifyIntent(input.message);
    }),

  // Save a detected journal entry from chat
  saveJournalFromChat: publicProcedure
    .input(
      z.object({
        conversationId: z.string(),
        messageId: z.string().optional(),
        title: z.string(),
        content: z.string(),
        tags: z.array(z.string()),
      })
    )
    .mutation(async ({ input }) => {
      const now = new Date();
      const id = ulid();

      await db.insert(schema.journalEntries).values({
        id,
        content: input.content,
        title: input.title,
        tags: JSON.stringify(input.tags),
        createdAt: now,
        updatedAt: now,
      });

      // Update message metadata to link to journal entry
      if (input.messageId) {
        const [msg] = await db
          .select()
          .from(schema.messages)
          .where(eq(schema.messages.id, input.messageId))
          .limit(1);

        if (msg) {
          const metadata = msg.metadata ? JSON.parse(msg.metadata) : {};
          metadata.savedAsJournal = id;
          await db
            .update(schema.messages)
            .set({ metadata: JSON.stringify(metadata) })
            .where(eq(schema.messages.id, input.messageId));
        }
      }

      return { id };
    }),

  // Save a detected calendar event from chat
  saveCalendarFromChat: publicProcedure
    .input(
      z.object({
        conversationId: z.string(),
        messageId: z.string().optional(),
        title: z.string(),
        description: z.string(),
        date: z.string(),
        time: z.string().optional(),
        tags: z.array(z.string()),
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
        tags: JSON.stringify(input.tags),
        createdAt: now,
      });

      // Update message metadata to link to calendar event
      if (input.messageId) {
        const [msg] = await db
          .select()
          .from(schema.messages)
          .where(eq(schema.messages.id, input.messageId))
          .limit(1);

        if (msg) {
          const metadata = msg.metadata ? JSON.parse(msg.metadata) : {};
          metadata.savedAsCalendar = id;
          await db
            .update(schema.messages)
            .set({ metadata: JSON.stringify(metadata) })
            .where(eq(schema.messages.id, input.messageId));
        }
      }

      return { id };
    }),

  // Get RAG context for a message (to be included in AI prompt)
  getRAGContext: publicProcedure
    .input(
      z.object({
        message: z.string(),
        limit: z.number().min(1).max(10).default(3),
      })
    )
    .query(async ({ input }) => {
      const configured = await isEmbeddingsConfigured();
      if (!configured) {
        return { context: '', results: [], isConfigured: false };
      }

      const results = await searchForContext(input.message, input.limit);
      const context = formatRAGContext(results);

      return {
        context,
        results: results.map((r) => ({
          sourceId: r.sourceId,
          sourceType: r.sourceType,
          title: r.metadata?.title,
          score: r.score,
          preview: r.content.slice(0, 200) + (r.content.length > 200 ? '...' : ''),
        })),
        isConfigured: true,
      };
    }),
});
