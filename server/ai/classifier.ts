import { generateObject } from 'ai';
import { z } from 'zod';
import { getModel } from './provider.js';

export type IntentType = 'journal' | 'calendar' | 'question' | 'conversation' | 'flashcard_request';

export interface ClassificationResult {
  intent: IntentType;
  confidence: number;
  journalEntry?: {
    title: string;
    content: string;
    tags: string[];
  };
  calendarEvent?: {
    title: string;
    description: string;
    date: string;
    time?: string;
    tags: string[];
  };
  flashcardRequest?: {
    topic: string;
  };
}

const classificationSchema = z.object({
  intent: z.enum(['journal', 'calendar', 'question', 'conversation', 'flashcard_request']),
  confidence: z.number().min(0).max(1),
  journalEntry: z.object({
    title: z.string(),
    content: z.string(),
    tags: z.array(z.string()),
  }).optional(),
  calendarEvent: z.object({
    title: z.string(),
    description: z.string(),
    date: z.string(),
    time: z.string().optional(),
    tags: z.array(z.string()),
  }).optional(),
  flashcardRequest: z.object({
    topic: z.string(),
  }).optional(),
});

const CLASSIFIER_PROMPT = `You are an intent classifier. Analyze the user's message and determine its intent.

Intent Types:
- journal: Personal reflections, diary entries, things learned, thoughts. "Today I...", "I learned...", "I felt..."
- calendar: Activities or events. "I went to...", "Had a meeting...", mentions of times/dates.
- question: Asking for information. Question marks, "How do I...", "What is..."
- flashcard_request: Wants flashcards or quiz. "quiz me", "create flashcards"
- conversation: General chat, greetings, commands.

Rules:
1. For journal entries, clean up content while preserving voice.
2. For calendar events, determine date. Today: {{TODAY}}
3. Confidence: 0.8+ for clear intents, 0.5-0.8 for ambiguous.
4. Generate relevant tags.`;

export async function classifyIntent(message: string): Promise<ClassificationResult> {
  const model = await getModel();
  const today = new Date().toISOString().split('T')[0];

  try {
    const result = await generateObject({
      model: model as Parameters<typeof generateObject>[0]['model'],
      schema: classificationSchema,
      prompt: `${CLASSIFIER_PROMPT.replace('{{TODAY}}', today)}\n\nUser message:\n${message}`,
    });
    return result.object as ClassificationResult;
  } catch (error) {
    console.error('Classification error:', error);
    return { intent: 'conversation', confidence: 0.5 };
  }
}

export function shouldClassify(message: string): boolean {
  const msg = message.toLowerCase().trim();
  if (msg.length < 20) return false;
  if (msg.startsWith('what ') || msg.startsWith('how ') || msg.startsWith('why ')) return false;
  if (['hi', 'hello', 'hey', 'thanks', 'bye'].includes(msg)) return false;
  return true;
}
