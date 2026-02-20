import { initTRPC } from '@trpc/server';
import { Context } from 'hono';

export interface TRPCContext {
  honoContext: Context;
  [key: string]: unknown;
}

const t = initTRPC.context<TRPCContext>().create();

export const router = t.router;
export const publicProcedure = t.procedure;
