import { createOpenAI } from '@ai-sdk/openai';
import { createGoogleGenerativeAI } from '@ai-sdk/google';
import { getSettings } from '../auth.js';

export type AIProvider = 'openai' | 'gemini' | 'ollama';

interface ProviderConfig {
  provider: AIProvider;
  model?: string;
  openaiApiKey?: string;
  googleApiKey?: string;
  ollamaUrl?: string;
  ollamaModel?: string;
}

const DEFAULT_MODELS: Record<AIProvider, string> = {
  openai: 'gpt-4o',
  gemini: 'gemini-2.0-flash',
  ollama: 'llama3.2',
};

export async function getProviderConfig(): Promise<ProviderConfig> {
  const settings = await getSettings();

  return {
    provider: (settings?.aiProvider as AIProvider) || 'openai',
    model: settings?.aiModel || undefined,
    openaiApiKey: settings?.openaiApiKey || process.env.OPENAI_API_KEY,
    googleApiKey: settings?.googleApiKey || process.env.GOOGLE_API_KEY,
    ollamaUrl: settings?.ollamaUrl || 'http://localhost:11434',
    ollamaModel: settings?.ollamaModel ?? undefined,
  };
}

export async function getModel() {
  const config = await getProviderConfig();

  switch (config.provider) {
    case 'openai': {
      if (!config.openaiApiKey) {
        throw new Error('OpenAI API key not configured');
      }
      const openai = createOpenAI({ apiKey: config.openaiApiKey });
      return openai(config.model || DEFAULT_MODELS.openai);
    }

    case 'gemini': {
      if (!config.googleApiKey) {
        throw new Error('Google API key not configured');
      }
      const google = createGoogleGenerativeAI({ apiKey: config.googleApiKey });
      return google(config.model || DEFAULT_MODELS.gemini);
    }

    case 'ollama': {
      // Ollama exposes an OpenAI-compatible API at /v1 — use @ai-sdk/openai
      // directly rather than the ollama-ai-provider package (which lags behind
      // the AI SDK spec version).
      const ollama = createOpenAI({
        baseURL: `${config.ollamaUrl}/v1`,
        apiKey: 'ollama', // required by the client but not validated by Ollama
      });
      return ollama(config.ollamaModel || config.model || DEFAULT_MODELS.ollama);
    }

    default:
      throw new Error(`Unknown AI provider: ${config.provider}`);
  }
}

export async function isAIConfigured(): Promise<boolean> {
  try {
    const config = await getProviderConfig();

    switch (config.provider) {
      case 'openai':
        return !!config.openaiApiKey;
      case 'gemini':
        return !!config.googleApiKey;
      case 'ollama':
        // Ollama is always "configured" if URL is set (we can't easily check if it's running)
        return !!config.ollamaUrl;
      default:
        return false;
    }
  } catch {
    return false;
  }
}
