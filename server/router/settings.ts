import { z } from 'zod';
import { router, publicProcedure } from './trpc.js';
import { db, schema } from '../db/index.js';
import { eq } from 'drizzle-orm';
import {
  isSetupComplete,
  setupPassword,
  verifyPassword,
  generateToken,
  setAuthCookie,
  clearAuthCookie,
  getSettings,
} from '../auth.js';

export const settingsRouter = router({
  // Check if setup is complete
  isSetupComplete: publicProcedure.query(async () => {
    return isSetupComplete();
  }),

  // Setup password (first time only)
  setup: publicProcedure
    .input(z.object({ password: z.string().min(8) }))
    .mutation(async ({ input, ctx }) => {
      const alreadySetup = await isSetupComplete();
      if (alreadySetup) {
        throw new Error('Setup already complete');
      }

      await setupPassword(input.password);
      const token = generateToken();
      setAuthCookie(ctx.honoContext, token);

      return { success: true };
    }),

  // Login
  login: publicProcedure
    .input(z.object({ password: z.string() }))
    .mutation(async ({ input, ctx }) => {
      const valid = await verifyPassword(input.password);
      if (!valid) {
        throw new Error('Invalid password');
      }

      const token = generateToken();
      setAuthCookie(ctx.honoContext, token);

      return { success: true };
    }),

  // Logout
  logout: publicProcedure.mutation(async ({ ctx }) => {
    clearAuthCookie(ctx.honoContext);
    return { success: true };
  }),

  // Get current settings (excluding sensitive data)
  get: publicProcedure.query(async () => {
    const settings = await getSettings();
    if (!settings) return null;

    return {
      aiProvider: settings.aiProvider,
      aiModel: settings.aiModel,
      hasOpenaiKey: !!settings.openaiApiKey,
      hasGoogleKey: !!settings.googleApiKey,
      ollamaUrl: settings.ollamaUrl,
      ollamaModel: settings.ollamaModel,
    };
  }),

  // Update AI provider settings
  updateAI: publicProcedure
    .input(
      z.object({
        aiProvider: z.enum(['openai', 'gemini', 'ollama']).optional(),
        aiModel: z.string().optional(),
        openaiApiKey: z.string().optional(),
        googleApiKey: z.string().optional(),
        ollamaUrl: z.string().optional(),
        ollamaModel: z.string().optional(),
      })
    )
    .mutation(async ({ input }) => {
      const now = new Date();
      const existing = await getSettings();

      const values: Record<string, unknown> = { updatedAt: now };

      if (input.aiProvider !== undefined) values.aiProvider = input.aiProvider;
      if (input.aiModel !== undefined) values.aiModel = input.aiModel;
      if (input.openaiApiKey !== undefined) values.openaiApiKey = input.openaiApiKey;
      if (input.googleApiKey !== undefined) values.googleApiKey = input.googleApiKey;
      if (input.ollamaUrl !== undefined) values.ollamaUrl = input.ollamaUrl;
      if (input.ollamaModel !== undefined) values.ollamaModel = input.ollamaModel;

      if (existing) {
        await db.update(schema.settings).set(values).where(eq(schema.settings.id, 1));
      } else {
        await db.insert(schema.settings).values({
          id: 1,
          ...values,
          createdAt: now,
        } as typeof schema.settings.$inferInsert);
      }

      return { success: true };
    }),

  // Change password
  changePassword: publicProcedure
    .input(
      z.object({
        currentPassword: z.string(),
        newPassword: z.string().min(8),
      })
    )
    .mutation(async ({ input }) => {
      const valid = await verifyPassword(input.currentPassword);
      if (!valid) {
        throw new Error('Current password is incorrect');
      }

      await setupPassword(input.newPassword);
      return { success: true };
    }),
});
