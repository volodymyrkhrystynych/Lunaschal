import { createTRPCReact, httpBatchLink } from '@trpc/react-query';
import type { AppRouter } from '../../server/router/index';

export const trpc = createTRPCReact<AppRouter>();

export const trpcClient = trpc.createClient({
  links: [
    httpBatchLink({
      url: '/api/trpc',
      async fetch(url, options) {
        const response = await globalThis.fetch(url as string, {
          ...options as RequestInit,
          credentials: 'include',
        });
        return response;
      },
    }),
  ],
});
