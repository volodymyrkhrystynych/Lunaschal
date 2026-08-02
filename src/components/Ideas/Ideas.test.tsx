// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Ideas } from './index';
import { api, type Idea, type IdeaSummary } from '../../hooks/api';

vi.mock('../../hooks/api', () => ({
  api: {
    ideas: {
      list: vi.fn(),
      get: vi.fn(),
      create: vi.fn(),
      createFromVoice: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
      listSketches: vi.fn(),
      addSketch: vi.fn(),
      updateSketch: vi.fn(),
      removeSketch: vi.fn(),
      paperPages: vi.fn(),
    },
  },
}));

// The recorder owns getUserMedia/MediaRecorder, neither of which exists in
// jsdom; the capture box's own behaviour is what's under test here.
const recorderState = {
  status: 'idle' as string,
  start: vi.fn(),
  stop: vi.fn(),
};
vi.mock('../../hooks/useRecorder', () => ({
  useRecorder: (onTranscript: (t: string) => void) => {
    recorderState.start = vi.fn(() => onTranscript('a spoken idea'));
    return { ...recorderState, error: '' };
  },
}));

vi.mock('../../shortcuts/ShortcutProvider', () => ({
  useShortcutScope: vi.fn(),
  useShortcuts: () => ({ level: 1 }),
}));

function summary(over: Partial<IdeaSummary> = {}): IdeaSummary {
  return {
    id: 'i1',
    title: 'Habit tracking',
    status: 'new',
    tags: '["ui"]',
    sketchCount: 0,
    createdAt: '2026-08-01T00:00:00Z',
    updatedAt: '2026-08-01T00:00:00Z',
    ...over,
  };
}

function detail(over: Partial<Idea> = {}): Idea {
  return {
    id: 'i1',
    title: 'Habit tracking',
    status: 'new',
    tags: '["ui"]',
    rawContent: 'a habit grid in the day view',
    content: '',
    createdAt: '2026-08-01T00:00:00Z',
    updatedAt: '2026-08-01T00:00:00Z',
    ...over,
  };
}

function renderIt() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <Ideas />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  recorderState.status = 'idle';
  vi.mocked(api.ideas.list).mockResolvedValue([summary()]);
  vi.mocked(api.ideas.get).mockResolvedValue(detail());
  vi.mocked(api.ideas.listSketches).mockResolvedValue([]);
  vi.mocked(api.ideas.paperPages).mockResolvedValue([]);
  vi.mocked(api.ideas.createFromVoice).mockResolvedValue({ id: 'i2' });
  vi.mocked(api.ideas.update).mockResolvedValue({ success: true });
});

describe('Ideas', () => {
  it('lists ideas with their status and tags', async () => {
    renderIt();
    expect(await screen.findByText('Habit tracking')).toBeTruthy();
    expect(screen.getByText('New')).toBeTruthy();
    // The tag appears both as a filter pill and on the row.
    expect(screen.getAllByText(/#ui/).length).toBeGreaterThan(0);
  });

  it('prompts to select before an idea is chosen', async () => {
    renderIt();
    expect(
      await screen.findByText(/Select an idea, or capture a new one/)
    ).toBeTruthy();
  });

  it('opens the detail pane when an idea is selected', async () => {
    renderIt();
    fireEvent.click(await screen.findByText('Habit tracking'));
    await waitFor(() => expect(api.ideas.get).toHaveBeenCalledWith('i1'));
    // The body falls back to the captured text until a polished version exists.
    expect(
      await screen.findByDisplayValue('a habit grid in the day view')
    ).toBeTruthy();
  });

  it('shows an empty state when there are no ideas', async () => {
    vi.mocked(api.ideas.list).mockResolvedValue([]);
    renderIt();
    expect(await screen.findByText('No ideas yet.')).toBeTruthy();
  });

  it('filters the list by tag pill', async () => {
    vi.mocked(api.ideas.list).mockResolvedValue([
      summary({ id: 'i1', title: 'Habit tracking', tags: '["ui"]' }),
      summary({ id: 'i2', title: 'Encrypted backups', tags: '["backend"]' }),
    ]);
    renderIt();
    fireEvent.click(await screen.findByText('#ui 1'));
    await waitFor(() =>
      expect(screen.queryByText('Encrypted backups')).toBeNull()
    );
    expect(screen.getByText('Habit tracking')).toBeTruthy();
  });
});

describe('IdeaCapture', () => {
  it('appends a transcript to the box instead of saving straight away', async () => {
    renderIt();
    await screen.findByText('Habit tracking');

    fireEvent.click(screen.getByRole('button', { name: 'Record an idea' }));
    expect(await screen.findByDisplayValue('a spoken idea')).toBeTruthy();
    // Dictation is editable before it becomes an idea.
    expect(api.ideas.createFromVoice).not.toHaveBeenCalled();
  });

  it('saves the captured text and selects the new idea', async () => {
    renderIt();
    await screen.findByText('Habit tracking');

    const box = screen.getByPlaceholderText(/Capture an idea/);
    fireEvent.change(box, { target: { value: 'a new thought' } });
    fireEvent.click(screen.getByRole('button', { name: /Save idea/ }));

    await waitFor(() =>
      expect(api.ideas.createFromVoice).toHaveBeenCalledWith('a new thought')
    );
    await waitFor(() => expect(api.ideas.get).toHaveBeenCalledWith('i2'));
  });

  it('will not save an empty or whitespace-only idea', async () => {
    renderIt();
    await screen.findByText('Habit tracking');

    const save = screen.getByRole('button', { name: /Save idea/ });
    expect(save.hasAttribute('disabled')).toBe(true);

    fireEvent.change(screen.getByPlaceholderText(/Capture an idea/), {
      target: { value: '   ' },
    });
    expect(save.hasAttribute('disabled')).toBe(true);
    expect(api.ideas.createFromVoice).not.toHaveBeenCalled();
  });
});

describe('sketches', () => {
  it('says the agent reads the caption, not the drawing', async () => {
    renderIt();
    fireEvent.click(await screen.findByText('Habit tracking'));
    expect(
      await screen.findByText(/the agent reads the note, not the drawing/i)
    ).toBeTruthy();
  });

  it('renders a borrowed page from its snapshot URL', async () => {
    vi.mocked(api.ideas.listSketches).mockResolvedValue([
      {
        id: 's1',
        ideaId: 'i1',
        pageId: 'p1',
        paperId: 'd1',
        caption: 'two-panel layout',
        position: 0,
        imageUrl: '/api/paper/pages/p1/image?v=123',
        createdAt: '2026-08-01T00:00:00Z',
      },
    ]);
    renderIt();
    fireEvent.click(await screen.findByText('Habit tracking'));

    const img = (await screen.findByAltText(
      'two-panel layout'
    )) as HTMLImageElement;
    expect(img.getAttribute('src')).toBe('/api/paper/pages/p1/image?v=123');
  });
});
