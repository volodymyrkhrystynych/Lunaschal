import { generateObject } from 'ai';
import { z } from 'zod';
import { getModel } from './provider.js';

export interface GeneratedFlashcard {
  front: string;
  back: string;
}

const flashcardsSchema = z.object({
  flashcards: z.array(
    z.object({
      front: z.string().describe('The question or prompt side of the flashcard'),
      back: z.string().describe('The answer or explanation side of the flashcard'),
    })
  ),
});

const GENERATOR_PROMPT = `You are a flashcard generator. Given content (like a journal entry or learning notes), create effective flashcards for spaced repetition learning.

**Guidelines:**
1. Extract key facts, concepts, definitions, and relationships
2. Create clear, concise questions that test understanding
3. Answers should be brief but complete
4. Use different question types: definitions, fill-in-the-blank, cause-effect, comparisons
5. Focus on the most important and memorable information
6. Avoid trivial or obvious questions
7. Each flashcard should test ONE concept

**Question Types to Use:**
- "What is...?" for definitions
- "Why does...?" for explanations
- "How does...?" for processes
- "What is the difference between...?" for comparisons
- "What happens when...?" for cause-effect
- Fill in the blank: "_____ is the process of..."

Generate 3-7 flashcards depending on the content density.`;

export async function generateFlashcardsFromContent(
  content: string,
  title?: string
): Promise<GeneratedFlashcard[]> {
  const model = await getModel();

  const contextInfo = title ? `Title: ${title}\n\nContent:\n${content}` : content;

  try {
    const result = await generateObject({
      model: model as Parameters<typeof generateObject>[0]['model'],
      schema: flashcardsSchema,
      prompt: `${GENERATOR_PROMPT}\n\n**Content to create flashcards from:**\n${contextInfo}`,
    });

    return result.object.flashcards;
  } catch (error) {
    console.error('Flashcard generation error:', error);
    throw new Error('Failed to generate flashcards');
  }
}

// Generate flashcards for a specific topic (for "quiz me on X" command)
export async function generateFlashcardsForTopic(
  topic: string,
  context?: string
): Promise<GeneratedFlashcard[]> {
  const model = await getModel();

  const TOPIC_PROMPT = `You are a flashcard generator. Create flashcards to help someone learn about the given topic.

**Guidelines:**
1. Cover key concepts, definitions, and facts about the topic
2. Create clear questions that test understanding
3. Answers should be accurate and concise
4. Include a mix of basic and more advanced questions
5. Generate 5-8 flashcards

${context ? `**Additional context from user's notes:**\n${context}\n\n` : ''}`;

  try {
    const result = await generateObject({
      model: model as Parameters<typeof generateObject>[0]['model'],
      schema: flashcardsSchema,
      prompt: `${TOPIC_PROMPT}**Topic:** ${topic}`,
    });

    return result.object.flashcards;
  } catch (error) {
    console.error('Flashcard generation error:', error);
    throw new Error('Failed to generate flashcards');
  }
}
