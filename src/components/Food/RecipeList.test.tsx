// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ShortcutProvider } from '../../shortcuts/ShortcutProvider';
import { RecipeList } from './RecipeList';
import { api, type Recipe } from '../../hooks/api';

vi.mock('../../hooks/api', () => ({
  api: {
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
    cookbook: {
      list: vi.fn(),
      search: vi.fn(),
      tags: vi.fn(),
      get: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      delete: vi.fn(),
      importRecipe: vi.fn(),
      generate: vi.fn(),
      addMedia: vi.fn(),
      deleteMedia: vi.fn(),
    },
  },
}));

let dictate: (text: string) => void = () => {};
vi.mock('../../hooks/useRecorder', () => ({
  useRecorder: (onTranscript: (text: string) => void) => {
    dictate = onTranscript;
    return { status: 'idle', error: '', start: vi.fn(), stop: vi.fn() };
  },
}));

function recipe(over: Partial<Recipe> = {}): Recipe {
  return {
    id: 'r1',
    title: "Grandma's Borscht",
    content: '## Ingredients\n- Beets\n- Beef\n\n## Instructions\n1. Simmer',
    tags: '["soup"]',
    sourceUrl: null,
    media: [],
    createdAt: '2026-07-30T12:00:00Z',
    updatedAt: '2026-07-30T12:00:00Z',
    ...over,
  };
}

function renderIt() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <ShortcutProvider currentView="food" onViewChange={() => {}}>
        <RecipeList />
      </ShortcutProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.cookbook.tags).mockResolvedValue([]);
  vi.mocked(api.cookbook.list).mockResolvedValue([recipe()]);
});

describe('RecipeList view mode', () => {
  it('renders markdown headers rather than the raw ## text', async () => {
    renderIt();
    fireEvent.click(await screen.findByText("Grandma's Borscht"));

    expect(
      await screen.findByRole('heading', { name: 'Ingredients' })
    ).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Instructions' })).toBeTruthy();
    // The raw markdown marker itself should not appear as literal text.
    expect(screen.queryByText('## Ingredients')).toBeNull();
  });

  it('does not enter edit mode on a title click', async () => {
    renderIt();
    fireEvent.click(await screen.findByText("Grandma's Borscht"));
    await screen.findByRole('heading', { name: 'Ingredients' });

    expect(screen.queryByPlaceholderText('Recipe title...')).toBeNull();
  });

  it('shows the edit textarea only after clicking Edit', async () => {
    renderIt();
    fireEvent.click(await screen.findByText('Edit'));

    expect(await screen.findByDisplayValue(/Beets/)).toBeTruthy();
  });

  it('renders a media gallery when the recipe has photos', async () => {
    vi.mocked(api.cookbook.list).mockResolvedValue([
      recipe({
        media: [
          {
            id: 'm1',
            kind: 'image',
            position: 0,
            url: '/api/cookbook/media/m1',
          },
        ],
      }),
    ]);
    const { container } = renderIt();
    fireEvent.click(await screen.findByText("Grandma's Borscht"));

    await waitFor(() => expect(container.querySelector('img')).toBeTruthy());
    expect(container.querySelector('img')?.getAttribute('src')).toBe(
      '/api/cookbook/media/m1'
    );
  });

  it('renders an audio player for audio media', async () => {
    vi.mocked(api.cookbook.list).mockResolvedValue([
      recipe({
        media: [
          {
            id: 'm2',
            kind: 'audio',
            position: 0,
            url: '/api/cookbook/media/m2',
          },
        ],
      }),
    ]);
    const { container } = renderIt();
    fireEvent.click(await screen.findByText("Grandma's Borscht"));

    await waitFor(() => expect(container.querySelector('audio')).toBeTruthy());
    expect(container.querySelector('audio')?.getAttribute('src')).toBe(
      '/api/cookbook/media/m2'
    );
  });
});

describe('RecipeList AI generation', () => {
  it('generates a recipe from a free-form prompt and expands it', async () => {
    vi.mocked(api.cookbook.generate).mockResolvedValue({
      id: 'r2',
      recipe: recipe({ id: 'r2', title: 'Vegan Chocolate Cake' }),
    });
    renderIt();
    fireEvent.click(await screen.findByText('✨ Generate'));

    const prompt = await screen.findByPlaceholderText(/Describe what you want/);
    fireEvent.change(prompt, { target: { value: 'vegan chocolate cake' } });
    fireEvent.click(screen.getByText('Generate'));

    await waitFor(() =>
      expect(api.cookbook.generate).toHaveBeenCalledWith('vegan chocolate cake')
    );
  });
});

describe('RecipeList new-recipe capture', () => {
  it('dictation appends to the content field', async () => {
    renderIt();
    fireEvent.click(await screen.findByText('+ New Recipe'));
    const content = await screen.findByPlaceholderText(/## Ingredients/);
    fireEvent.change(content, { target: { value: 'existing text' } });
    dictate('dictated more');
    await waitFor(() =>
      expect((content as HTMLTextAreaElement).value).toBe(
        'existing text dictated more'
      )
    );
  });

  it('submits staged media files on save', async () => {
    vi.mocked(api.cookbook.create).mockResolvedValue(recipe());
    const { container } = renderIt();
    fireEvent.click(await screen.findByText('+ New Recipe'));

    fireEvent.change(await screen.findByPlaceholderText('Recipe title...'), {
      target: { value: 'New Dish' },
    });
    fireEvent.change(screen.getByPlaceholderText(/## Ingredients/), {
      target: { value: 'stuff' },
    });

    const file = new File(['x'], 'photo.jpg', { type: 'image/jpeg' });
    const fileInput = container.querySelector(
      'input[accept="image/*"]'
    ) as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [file] } });

    fireEvent.click(screen.getByText('Save'));

    await waitFor(() => expect(api.cookbook.create).toHaveBeenCalled());
    const call = vi.mocked(api.cookbook.create).mock.calls[0][0];
    expect(call.title).toBe('New Dish');
    expect(call.media).toEqual([file]);
  });
});
