import { Hono, Context } from 'hono';
import { serve } from '@hono/node-server';
import { cors } from 'hono/cors';
import { logger } from 'hono/logger';
import { serveStatic } from '@hono/node-server/serve-static';
import { trpcServer } from '@hono/trpc-server';
import { appRouter, TRPCContext } from './router/index.js';
import { db, runMigrations } from './db/index.js';
import { chatStream } from './ai/chat.js';
import { isAIConfigured } from './ai/provider.js';

const app = new Hono();

// Middleware
app.use('*', logger());
app.use(
  '/api/*',
  cors({
    origin: ['http://localhost:5173', 'http://localhost:7842'],
    credentials: true,
  })
);

// tRPC endpoint
app.use(
  '/api/trpc/*',
  trpcServer({
    router: appRouter,
    createContext: (_opts, c): TRPCContext => ({
      honoContext: c,
    }),
  })
);

// Streaming chat endpoint (separate from tRPC due to SSE)
app.post('/api/chat/stream', async (c) => {
  const configured = await isAIConfigured();
  if (!configured) {
    return c.json({ error: 'AI provider not configured' }, 400);
  }

  const body = await c.req.json();
  const { messages, ragContext, systemPrompt } = body as {
    messages: Array<{ role: 'user' | 'assistant'; content: string }>;
    ragContext?: string;
    systemPrompt?: string;
  };

  if (!messages || !Array.isArray(messages)) {
    return c.json({ error: 'Messages array required' }, 400);
  }

  // Set up SSE response
  c.header('Content-Type', 'text/event-stream');
  c.header('Cache-Control', 'no-cache');
  c.header('Connection', 'keep-alive');

  const stream = new ReadableStream({
    async start(controller) {
      try {
        for await (const chunk of chatStream(messages, { ragContext, systemPrompt })) {
          const data = JSON.stringify({ content: chunk });
          controller.enqueue(new TextEncoder().encode(`data: ${data}\n\n`));
        }
        controller.enqueue(new TextEncoder().encode('data: [DONE]\n\n'));
        controller.close();
      } catch (error) {
        const errorMessage = error instanceof Error ? error.message : 'Unknown error';
        controller.enqueue(
          new TextEncoder().encode(`data: ${JSON.stringify({ error: errorMessage })}\n\n`)
        );
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    },
  });
});

// Transcription endpoint — proxies to the local faster-whisper STT service
app.post('/api/transcribe', async (c) => {
  const sttUrl = process.env.STT_SERVICE_URL || 'http://127.0.0.1:8765';

  try {
    const formData = await c.req.formData();

    const response = await fetch(`${sttUrl}/transcribe`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      const detail = await response.text();
      return c.json({ error: detail }, response.status as 400 | 500 | 503);
    }

    return c.json(await response.json());
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    if (msg.includes('ECONNREFUSED') || msg.includes('fetch failed')) {
      return c.json(
        { error: 'STT service not running. Start it with: ./stt/run_service.sh' },
        503
      );
    }
    return c.json({ error: msg }, 500);
  }
});

// Health check
app.get('/api/health', (c) => {
  return c.json({ status: 'ok', timestamp: new Date().toISOString() });
});

// In production, serve static files
if (process.env.NODE_ENV === 'production') {
  app.use('/*', serveStatic({ root: './dist' }));
  app.get('*', serveStatic({ path: './dist/index.html' }));
}

// Preload the Ollama model and keep it alive indefinitely so there is no
// cold-start lag on the first chat request.
async function keepOllamaAlive() {
  const { getProviderConfig } = await import('./ai/provider.js');
  const config = await getProviderConfig();
  if (config.provider !== 'ollama') return;

  const model = config.ollamaModel || 'llama3.2';
  const base  = config.ollamaUrl   || 'http://localhost:11434';

  try {
    // keep_alive: -1 tells Ollama to never unload this model from VRAM
    await fetch(`${base}/api/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, keep_alive: -1, stream: false }),
    });
    console.log(`Ollama: model '${model}' loaded and kept alive.`);
  } catch {
    console.warn(`Ollama: could not preload '${model}' — is Ollama running?`);
  }

  // Re-ping every 4 minutes so the model stays resident even if Ollama's
  // default keep_alive timer fires before the next real request.
  setInterval(async () => {
    try {
      await fetch(`${base}/api/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model, keep_alive: -1, stream: false }),
      });
    } catch { /* ignore — Ollama may be restarting */ }
  }, 4 * 60 * 1000);
}

// Initialize database and start server
async function main() {
  console.log('Running database migrations...');
  runMigrations();
  console.log('Migrations complete.');

  const port = Number(process.env.PORT) || 7842;
  console.log(`Server starting on http://localhost:${port}`);

  serve({
    fetch: app.fetch,
    port,
  });

  // Fire-and-forget — don't block server startup
  keepOllamaAlive();
}

main().catch((err) => {
  console.error('Failed to start server:', err);
  process.exit(1);
});
