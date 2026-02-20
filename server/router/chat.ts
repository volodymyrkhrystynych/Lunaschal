import { z } from 'zod';
import { router, publicProcedure } from './trpc.js';
import { db, schema } from '../db/index.js';
import { eq, desc } from 'drizzle-orm';
import { ulid } from 'ulid';

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
});
