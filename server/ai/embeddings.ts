import { embed, embedMany } from 'ai';
import { createOpenAI } from '@ai-sdk/openai';
import { createGoogleGenerativeAI } from '@ai-sdk/google';
import { getProviderConfig, type AIProvider } from './provider.js';

const EMBEDDING_MODELS: Record<AIProvider, string> = {
  openai: 'text-embedding-3-small',
  gemini: 'text-embedding-004',
  ollama: 'nomic-embed-text',
};

// Chunk size for splitting long texts (in characters)
const CHUNK_SIZE = 1000;
const CHUNK_OVERLAP = 200;

export interface EmbeddingResult {
  embedding: number[];
  chunkIndex: number;
  chunkText: string;
}

async function getEmbeddingModel() {
  const config = await getProviderConfig();

  switch (config.provider) {
    case 'openai': {
      if (!config.openaiApiKey) {
        throw new Error('OpenAI API key not configured for embeddings');
      }
      const openai = createOpenAI({ apiKey: config.openaiApiKey });
      return openai.embedding(EMBEDDING_MODELS.openai);
    }

    case 'gemini': {
      if (!config.googleApiKey) {
        throw new Error('Google API key not configured for embeddings');
      }
      const google = createGoogleGenerativeAI({ apiKey: config.googleApiKey });
      return google.textEmbeddingModel(EMBEDDING_MODELS.gemini);
    }

    case 'ollama': {
      // For Ollama, we'll use OpenAI-compatible embeddings if available
      // Otherwise fall back to a simple approach
      throw new Error('Ollama embeddings not yet supported. Use OpenAI or Gemini for RAG.');
    }

    default:
      throw new Error(`Unknown AI provider: ${config.provider}`);
  }
}

function splitIntoChunks(text: string): string[] {
  const chunks: string[] = [];

  // If text is short enough, return as single chunk
  if (text.length <= CHUNK_SIZE) {
    return [text];
  }

  let start = 0;
  while (start < text.length) {
    let end = start + CHUNK_SIZE;

    // Try to break at sentence or paragraph boundary
    if (end < text.length) {
      const lastPeriod = text.lastIndexOf('.', end);
      const lastNewline = text.lastIndexOf('\n', end);
      const breakPoint = Math.max(lastPeriod, lastNewline);

      if (breakPoint > start + CHUNK_SIZE / 2) {
        end = breakPoint + 1;
      }
    }

    chunks.push(text.slice(start, Math.min(end, text.length)).trim());
    start = end - CHUNK_OVERLAP;

    // Prevent infinite loop
    if (start >= text.length - CHUNK_OVERLAP) {
      break;
    }
  }

  return chunks.filter((chunk) => chunk.length > 0);
}

export async function generateEmbedding(text: string): Promise<number[]> {
  const model = await getEmbeddingModel();

  const result = await embed({
    model: model as Parameters<typeof embed>[0]['model'],
    value: text,
  });

  return result.embedding;
}

export async function generateEmbeddings(text: string): Promise<EmbeddingResult[]> {
  const model = await getEmbeddingModel();
  const chunks = splitIntoChunks(text);

  if (chunks.length === 0) {
    return [];
  }

  if (chunks.length === 1) {
    const result = await embed({
      model: model as Parameters<typeof embed>[0]['model'],
      value: chunks[0],
    });

    return [{
      embedding: result.embedding,
      chunkIndex: 0,
      chunkText: chunks[0],
    }];
  }

  // Embed multiple chunks at once for efficiency
  const result = await embedMany({
    model: model as Parameters<typeof embedMany>[0]['model'],
    values: chunks,
  });

  return result.embeddings.map((embedding, index) => ({
    embedding,
    chunkIndex: index,
    chunkText: chunks[index],
  }));
}

export async function isEmbeddingsConfigured(): Promise<boolean> {
  try {
    const config = await getProviderConfig();

    switch (config.provider) {
      case 'openai':
        return !!config.openaiApiKey;
      case 'gemini':
        return !!config.googleApiKey;
      case 'ollama':
        return false; // Not yet supported
      default:
        return false;
    }
  } catch {
    return false;
  }
}
