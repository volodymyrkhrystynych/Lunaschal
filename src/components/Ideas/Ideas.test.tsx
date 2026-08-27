// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Ideas } from './index';
import { api, type Idea, type IdeaSummary, type Repo } from '../../hooks/api';

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
      assess: vi.fn(),
      listQuestions: vi.fn(),
      answerQuestion: vi.fn(),
      listConversations: vi.fn(),
      createConversation: vi.fn(),
      listPlans: vi.fn(),
      getPlan: vi.fn(),
      createPlan: vi.fn(),
    },
    chat: { getConversation: vi.fn() },
    repos: { list: vi.fn() },
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
    return { ...recorderState, error: '', canTranscribe: true };
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
    openQuestionCount: 0,
    articleCount: 0,
    hasPlan: false,
    verdict: null,
    confidence: null,
    effort: null,
    onRoadmap: false,
    assessmentStale: false,
    userVerdict: null,
    researchState: 'idle',
    repoId: null,
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
    userVerdict: null,
    userVerdictNote: null,
    researchState: 'idle',
    repoId: null,
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
  // The repo selection is persisted per device now, so it survives an unmount.
  // Without this, a test that changes the dropdown silently sets the starting
  // filter for the next one.
  localStorage.clear();
  vi.mocked(api.ideas.list).mockResolvedValue([summary()]);
  vi.mocked(api.ideas.get).mockResolvedValue(detail());
  vi.mocked(api.ideas.listSketches).mockResolvedValue([]);
  vi.mocked(api.ideas.paperPages).mockResolvedValue([]);
  vi.mocked(api.ideas.createFromVoice).mockResolvedValue({ id: 'i2' });
  vi.mocked(api.ideas.update).mockResolvedValue({ success: true });
  vi.mocked(api.ideas.listQuestions).mockResolvedValue([]);
  vi.mocked(api.ideas.listConversations).mockResolvedValue([]);
  vi.mocked(api.ideas.listPlans).mockResolvedValue([]);
  vi.mocked(api.repos.list).mockResolvedValue([]);
});

function repo(over: Partial<Repo> = {}): Repo {
  return {
    id: 'r1',
    slug: 'lunaschal',
    name: 'Lunaschal',
    remoteUrl: 'https://github.com/o/lunaschal.git',
    branch: 'main',
    cloneState: 'ready',
    cloneError: null,
    headSha: 'abc1234',
    lastPulledAt: null,
    graphBuiltAt: null,
    graphNodeCount: null,
    isDefault: true,
    hasCheckout: true,
    hasGraph: false,
    ...over,
  };
}

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

    // The id is minted here, not waited for: the capture is queued (it may be
    // sitting in the offline queue right now) and the idea still opens.
    await waitFor(() => expect(api.ideas.createFromVoice).toHaveBeenCalled());
    const [rawContent, clientId] = vi.mocked(api.ideas.createFromVoice).mock
      .calls[0] as [string, string];
    expect(rawContent).toBe('a new thought');
    expect(clientId).toMatch(/^[0-9A-HJKMNP-TV-Z]{26}$/);
    await waitFor(() => expect(api.ideas.get).toHaveBeenCalledWith(clientId));
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

describe('stat chips', () => {
  it('shows the agent verdict with its confidence', async () => {
    vi.mocked(api.ideas.list).mockResolvedValue([
      summary({ verdict: 'partial', confidence: 0.62 }),
    ]);
    renderIt();
    expect(await screen.findByText('Partly built 62%')).toBeTruthy();
  });

  it("shows the user's verdict instead, without a confidence", async () => {
    vi.mocked(api.ideas.list).mockResolvedValue([
      summary({ verdict: 'no', confidence: 0.9, userVerdict: 'yes' }),
    ]);
    renderIt();
    expect(await screen.findByText('Already built (you)')).toBeTruthy();
    expect(screen.queryByText(/90%/)).toBeNull();
  });

  it('marks a verdict stale once the repo has moved on', async () => {
    vi.mocked(api.ideas.list).mockResolvedValue([
      summary({ verdict: 'yes', confidence: 0.8, assessmentStale: true }),
    ]);
    renderIt();
    expect(await screen.findByText(/stale/)).toBeTruthy();
  });

  it('counts open decisions, plans and research notes', async () => {
    vi.mocked(api.ideas.list).mockResolvedValue([
      summary({ openQuestionCount: 2, hasPlan: true, articleCount: 3 }),
    ]);
    renderIt();
    expect(await screen.findByTitle('2 decisions needed')).toBeTruthy();
    expect(screen.getByTitle('Has a plan')).toBeTruthy();
    expect(screen.getByTitle('3 research notes')).toBeTruthy();
  });

  it('shows no verdict chip before an assessment exists', async () => {
    renderIt();
    await screen.findByText('Habit tracking');
    expect(
      screen.queryByText(/Already built|Partly built|Not built/)
    ).toBeNull();
  });
});

describe('assessment pane', () => {
  it('renders the cited evidence so the verdict is checkable', async () => {
    vi.mocked(api.ideas.assess).mockResolvedValue({
      id: 'a1',
      ideaId: 'i1',
      snapshotId: 's1',
      verdict: 'partial',
      confidence: 0.6,
      rationale: 'Some of the machinery exists.',
      evidence: [
        {
          kind: 'table',
          ref: 'paper_pages',
          file: 'backend/db/schema.sql',
          line: 12,
          detail: null,
        },
      ],
      onRoadmap: [],
      effort: 'm',
      stale: false,
      assessedAt: '2026-08-01T00:00:00Z',
    });
    renderIt();
    fireEvent.click(await screen.findByText('Habit tracking'));
    fireEvent.click(
      await screen.findByRole('button', { name: 'Check the repo' })
    );

    expect(
      await screen.findByText('Some of the machinery exists.')
    ).toBeTruthy();
    expect(screen.getByText('paper_pages')).toBeTruthy();
    expect(screen.getByText('backend/db/schema.sql:12')).toBeTruthy();
  });

  it('lets the user override the verdict', async () => {
    renderIt();
    fireEvent.click(await screen.findByText('Habit tracking'));
    fireEvent.click(await screen.findByRole('button', { name: 'Built' }));
    await waitFor(() =>
      expect(api.ideas.update).toHaveBeenCalledWith('i1', {
        userVerdict: 'yes',
      })
    );
  });

  it('surfaces open decisions and records an answer', async () => {
    vi.mocked(api.ideas.listQuestions).mockResolvedValue([
      {
        id: 'q1',
        ideaId: 'i1',
        question: 'Where should sketches live?',
        why: 'storage',
        options: ['Paper', 'new table'],
        answer: null,
        status: 'open',
        answeredAt: null,
        createdAt: '2026-08-01T00:00:00Z',
      },
    ]);
    vi.mocked(api.ideas.answerQuestion).mockResolvedValue({ success: true });
    renderIt();
    fireEvent.click(await screen.findByText('Habit tracking'));

    expect(await screen.findByText('Needs a decision (1)')).toBeTruthy();
    const field = screen.getByLabelText('Answer: Where should sketches live?');
    fireEvent.blur(field, { target: { value: 'Paper pages' } });
    await waitFor(() =>
      expect(api.ideas.answerQuestion).toHaveBeenCalledWith('q1', {
        answer: 'Paper pages',
      })
    );
  });
});

describe('plan pane', () => {
  it('offers to create a plan when there is none', async () => {
    renderIt();
    fireEvent.click(await screen.findByText('Habit tracking'));
    expect(
      await screen.findByRole('button', { name: 'Create plan' })
    ).toBeTruthy();
    expect(screen.getByText(/No plan yet/)).toBeTruthy();
  });

  it('renders a generated plan and offers to copy it', async () => {
    vi.mocked(api.ideas.listPlans).mockResolvedValue([
      {
        id: 'p1',
        ideaId: 'i1',
        version: 1,
        createdAt: '2026-08-01T00:00:00Z',
        updatedAt: '2026-08-01T00:00:00Z',
      },
    ]);
    vi.mocked(api.ideas.getPlan).mockResolvedValue({
      id: 'p1',
      ideaId: 'i1',
      version: 1,
      content: '# Habit tracking\n\n## Data model',
      spec: '{}',
      createdAt: '2026-08-01T00:00:00Z',
      updatedAt: '2026-08-01T00:00:00Z',
    });
    renderIt();
    fireEvent.click(await screen.findByText('Habit tracking'));

    expect(
      await screen.findByRole('button', { name: 'Copy markdown' })
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Regenerate' })).toBeTruthy();
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

describe('the repository switcher', () => {
  it('is not shown when there is nothing to choose between', async () => {
    // One repo, or none, means a control that can only be set one way.
    vi.mocked(api.repos.list).mockResolvedValue([repo()]);
    renderIt();
    await screen.findByText('Habit tracking');
    expect(screen.queryByLabelText('Repository')).toBeNull();
  });

  it('filters the list down to one repository', async () => {
    vi.mocked(api.repos.list).mockResolvedValue([
      repo({ id: 'r1', name: 'Lunaschal' }),
      repo({ id: 'r2', name: 'Other', slug: 'other', isDefault: false }),
    ]);
    vi.mocked(api.ideas.list).mockResolvedValue([
      summary({ id: 'i1', title: 'Habit tracking', repoId: 'r1' }),
      summary({ id: 'i2', title: 'Other thing', repoId: 'r2' }),
    ]);
    renderIt();

    const select = (await screen.findByLabelText(
      'Repository'
    )) as HTMLSelectElement;
    expect(screen.getByText('Other thing')).toBeTruthy();

    fireEvent.change(select, { target: { value: 'r1' } });
    expect(screen.getByText('Habit tracking')).toBeTruthy();
    expect(screen.queryByText('Other thing')).toBeNull();
  });

  it('files a new idea under the repository being viewed', async () => {
    vi.mocked(api.repos.list).mockResolvedValue([
      repo({ id: 'r1' }),
      repo({ id: 'r2', name: 'Other', slug: 'other', isDefault: false }),
    ]);
    renderIt();

    const select = (await screen.findByLabelText(
      'Repository'
    )) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'r2' } });

    fireEvent.change(screen.getByPlaceholderText(/capture an idea/i), {
      target: { value: 'a new idea' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() =>
      expect(api.ideas.createFromVoice).toHaveBeenCalledWith(
        'a new idea',
        expect.any(String),
        'r2'
      )
    );
  });

  it('lets the server pick the repo while viewing all of them', async () => {
    vi.mocked(api.repos.list).mockResolvedValue([
      repo({ id: 'r1' }),
      repo({ id: 'r2', name: 'Other', slug: 'other', isDefault: false }),
    ]);
    renderIt();
    await screen.findByLabelText('Repository');

    fireEvent.change(screen.getByPlaceholderText(/capture an idea/i), {
      target: { value: 'unfiled' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() =>
      expect(api.ideas.createFromVoice).toHaveBeenCalledWith(
        'unfiled',
        expect.any(String),
        undefined
      )
    );
  });
});

describe('the repository selection persists', () => {
  it('is restored after a reload', async () => {
    // The app runs as an installed PWA; a backgrounded webview is re-executed
    // from scratch on the next screen-on, wiping React state.
    vi.mocked(api.repos.list).mockResolvedValue([
      repo({ id: 'r1' }),
      repo({ id: 'r2', name: 'Other', slug: 'other', isDefault: false }),
    ]);
    vi.mocked(api.ideas.list).mockResolvedValue([
      summary({ id: 'i1', title: 'Habit tracking', repoId: 'r1' }),
      summary({ id: 'i2', title: 'Other thing', repoId: 'r2' }),
    ]);

    const first = renderIt();
    fireEvent.change(await screen.findByLabelText('Repository'), {
      target: { value: 'r2' },
    });
    expect(screen.queryByText('Habit tracking')).toBeNull();
    first.unmount();

    renderIt();
    const select = (await screen.findByLabelText(
      'Repository'
    )) as HTMLSelectElement;
    expect(select.value).toBe('r2');
    expect(screen.getByText('Other thing')).toBeTruthy();
    expect(screen.queryByText('Habit tracking')).toBeNull();
  });

  it('still files new ideas under the remembered repo', async () => {
    localStorage.setItem('lunaschal:ideaRepo', 'r2');
    vi.mocked(api.repos.list).mockResolvedValue([
      repo({ id: 'r1' }),
      repo({ id: 'r2', name: 'Other', slug: 'other', isDefault: false }),
    ]);
    renderIt();
    await screen.findByLabelText('Repository');

    fireEvent.change(screen.getByPlaceholderText(/capture an idea/i), {
      target: { value: 'a remembered idea' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() =>
      expect(api.ideas.createFromVoice).toHaveBeenCalledWith(
        'a remembered idea',
        expect.any(String),
        'r2'
      )
    );
  });

  it('falls back to all when the remembered repo was removed', async () => {
    // Filtering to a repo that no longer exists leaves an empty list, and an
    // empty Ideas tab is indistinguishable from one with no ideas.
    localStorage.setItem('lunaschal:ideaRepo', 'deleted-repo');
    vi.mocked(api.repos.list).mockResolvedValue([
      repo({ id: 'r1' }),
      repo({ id: 'r2', name: 'Other', slug: 'other', isDefault: false }),
    ]);
    renderIt();

    const select = (await screen.findByLabelText(
      'Repository'
    )) as HTMLSelectElement;
    expect(select.value).toBe('all');
    expect(screen.getByText('Habit tracking')).toBeTruthy();
  });

  it('does not filter the list when the switcher is hidden', async () => {
    // One repo means no switcher — but a selection stored while there were two
    // must not go on silently hiding ideas with no control to undo it.
    localStorage.setItem('lunaschal:ideaRepo', 'r2');
    vi.mocked(api.repos.list).mockResolvedValue([repo({ id: 'r1' })]);
    vi.mocked(api.ideas.list).mockResolvedValue([
      summary({ id: 'i1', title: 'Habit tracking', repoId: 'r1' }),
    ]);
    renderIt();

    expect(await screen.findByText('Habit tracking')).toBeTruthy();
    expect(screen.queryByLabelText('Repository')).toBeNull();
  });
});
