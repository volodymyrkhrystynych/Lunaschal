import { streamText, generateText } from 'ai';
import { getModel } from './provider.js';

const SYSTEM_PROMPT = `You are Lunaschal, a helpful personal AI assistant. You help users with:
- Journaling and reflection
- Tracking activities and events
- Creating and reviewing flashcards for learning
- General questions and conversation

Be concise, helpful, and friendly. When users share personal experiences or daily activities,
acknowledge them warmly. If something seems like a journal entry or activity log, you can
offer to save it for them.

When context from the user's knowledge base is provided, use it to give more personalized and relevant responses.
Reference their past journal entries, activities, or notes naturally in your responses when relevant.`;

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface ChatOptions {
  ragContext?: string;
}

export async function chat(messages: ChatMessage[], options?: ChatOptions): Promise<string> {
  const model = await getModel();

  // Build system prompt with optional RAG context
  let systemContent = SYSTEM_PROMPT;
  if (options?.ragContext) {
    systemContent = `${SYSTEM_PROMPT}\n\n${options.ragContext}`;
  }

  const allMessages = [
    { role: 'system' as const, content: systemContent },
    ...messages.map((m) => ({
      role: m.role,
      content: m.content,
    })),
  ];

  const result = await generateText({
    model: model as Parameters<typeof generateText>[0]['model'],
    messages: allMessages,
  });

  return result.text;
}

export async function* chatStream(messages: ChatMessage[], options?: ChatOptions): AsyncGenerator<string> {
  const model = await getModel();

  // Build system prompt with optional RAG context
  let systemContent = SYSTEM_PROMPT;
  if (options?.ragContext) {
    systemContent = `${SYSTEM_PROMPT}\n\n${options.ragContext}`;
  }

  const allMessages = [
    { role: 'system' as const, content: systemContent },
    ...messages.map((m) => ({
      role: m.role,
      content: m.content,
    })),
  ];

  const result = streamText({
    model: model as Parameters<typeof streamText>[0]['model'],
    messages: allMessages,
  });

  for await (const chunk of result.textStream) {
    yield chunk;
  }
}
