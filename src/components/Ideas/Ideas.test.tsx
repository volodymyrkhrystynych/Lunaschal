// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
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
//
// `start` records what it was asked for and arms `finishRecording`, which
// stands in for the user pressing Stop: the real hook stores the clip and hands
// the caller the stored recording, idea id and all.
const recorderState = {
  status: 'idle' as string,
  start: vi.fn(),
  stop: vi.fn(),
};
interface StartCall {
  mode?: string;
  opts?: { durable?: boolean; idea?: { id: string; repoId?: string } };
}
let startCalls: StartCall[] = [];
let finishRecording: (() => void) | null = null;

vi.mock('../../hooks/useRecorder', () => ({
  useRecorder: (
    onTranscript: (t: string) => void,
    _onAudio: unknown,
    options: { onRecording?: (rec: unknown) => void } = {}
  ) => {
    recorderState.start = vi.fn((mode?: string, opts?: StartCall['opts']) => {
      startCalls.push({ mode, opts });
      // Durable captures deliver a stored recording when the user stops;
      // plain dictation (the discussion and decision boxes) delivers text.
      if (opts?.durable) {
        finishRecording = () =>
          options.onRecording?.({ id: 'rec-1', idea: opts?.idea });
      } else {
        onTranscript('a spoken idea');
      }
    });
    return { ...recorderState, error: '', canTranscribe: true };
  },
}));

// The durable upload queue. Its own behaviour is covered in
// src/offline/recordingQueue.test.ts; here it only has to be observable.
const captureIdeaRecording = vi.fn();
vi.mock('../../offline/recordingQueue', () => ({
  captureIdeaRecording: (...args: unknown[]) => {
    captureIdeaRecording(...args);
    return Promise.resolve();
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

function renderIt(props: Parameters<typeof Ideas>[0] = {}) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <Ideas {...props} />
    </QueryClientProvider>
  );
}

/** Open an idea and move to one of its tabs. The Decisions tab carries a
 *  count badge, so tabs are matched by prefix rather than exact name. */
async function openIdea(tab?: 'Idea' | 'Decisions' | 'Discuss' | 'Plan') {
  fireEvent.click(await screen.findByText('Habit tracking'));
  await screen.findByLabelText('Idea title');
  if (tab) {
    fireEvent.click(
      await screen.findByRole('tab', { name: new RegExp(`^${tab}`) })
    );
  }
}

/** `openIdea` for the tests that run on fake timers — findBy* never settles
 *  there, so each await is an explicit tick instead. */
async function openIdeaWithFakeTimers(label = 'Habit tracking') {
  await vi.advanceTimersByTimeAsync(20);
  fireEvent.click(screen.getByText(label));
  await vi.advanceTimersByTimeAsync(20);
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
  beforeEach(() => {
    startCalls = [];
    finishRecording = null;
    captureIdeaRecording.mockClear();
  });

  it('records durably, with the idea id minted before the first chunk', async () => {
    renderIt();
    await screen.findByText('Habit tracking');

    fireEvent.click(screen.getByRole('button', { name: 'Record an idea' }));

    // 'transcribe' + durable: the clip is stored on the device and the server
    // transcribes it, rather than the browser transcribing a clip it holds in
    // memory. The idea's id goes into the store with the audio so a phone that
    // dies mid-recording still knows what the clip was for.
    expect(startCalls).toHaveLength(1);
    expect(startCalls[0]!.mode).toBe('transcribe');
    expect(startCalls[0]!.opts?.durable).toBe(true);
    expect(startCalls[0]!.opts?.idea?.id).toMatch(/^[0-9A-HJKMNP-TV-Z]{26}$/);
  });

  it('stopping the recording is the save, and opens the idea', async () => {
    renderIt();
    await screen.findByText('Habit tracking');

    fireEvent.click(screen.getByRole('button', { name: 'Record an idea' }));
    const ideaId = startCalls[0]!.opts!.idea!.id;
    finishRecording!();

    // Queued as one durable upload — no text was transcribed in the browser,
    // and nothing was posted to the ideas endpoints.
    await waitFor(() => expect(captureIdeaRecording).toHaveBeenCalled());
    const rec = captureIdeaRecording.mock.calls[0]![1] as {
      idea?: { id: string };
    };
    expect(rec.idea?.id).toBe(ideaId);
    expect(api.ideas.createFromVoice).not.toHaveBeenCalled();

    // ...and the idea it will become is already open.
    await waitFor(() => expect(api.ideas.get).toHaveBeenCalledWith(ideaId));
  });

  it('files the recording under the repo the list is filtered to', async () => {
    vi.mocked(api.repos.list).mockResolvedValue([
      repo({ id: 'r1', name: 'Lunaschal' }),
      repo({ id: 'r2', name: 'Other' }),
    ]);
    renderIt();
    await screen.findByText('Habit tracking');

    fireEvent.change(await screen.findByLabelText('Repository'), {
      target: { value: 'r2' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Record an idea' }));

    expect(startCalls.at(-1)!.opts?.idea?.repoId).toBe('r2');
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

describe('an idea that was dictated', () => {
  // The audio belongs to the journal entry the same recording created — an
  // idea holds no files of its own — so the pane shows a pointer at it. Worth
  // having here: the body is a transcription, and when that comes back wrong
  // the recording is the only thing that still holds the idea.
  const recording = {
    entryId: 'e9',
    attachmentId: 'a9',
    url: '/api/journal/attachments/a9/file',
    transcriptStatus: 'done' as const,
    transcriptError: null,
  };

  it('plays the recording and links to the journal entry', async () => {
    vi.mocked(api.ideas.get).mockResolvedValue(detail({ recording }));
    const onOpenEntry = vi.fn();
    renderIt({ onOpenEntry });
    await openIdea();

    fireEvent.click(await screen.findByText('Journal entry ›'));
    expect(onOpenEntry).toHaveBeenCalledWith('e9');
    expect(
      document.querySelector('audio[src="/api/journal/attachments/a9/file"]')
    ).toBeTruthy();
  });

  it('says it is transcribing rather than looking like an empty idea', async () => {
    vi.mocked(api.ideas.get).mockResolvedValue(
      detail({
        title: '',
        rawContent: '',
        content: '',
        recording: { ...recording, transcriptStatus: 'running' },
      })
    );
    renderIt();
    await openIdea();

    expect(await screen.findByText('Transcribing…')).toBeTruthy();
  });

  it('shows a failed transcription, with the audio still there', async () => {
    vi.mocked(api.ideas.get).mockResolvedValue(
      detail({
        title: '',
        rawContent: '',
        content: '',
        recording: {
          ...recording,
          transcriptStatus: 'error',
          transcriptError: 'No speech found in the recording',
        },
      })
    );
    renderIt();
    await openIdea();

    expect(await screen.findByText(/No speech found/)).toBeTruthy();
    expect(document.querySelector('audio')).toBeTruthy();
  });

  it('opens straight onto the idea the Journal linked to', async () => {
    const onTargetConsumed = vi.fn();
    renderIt({ target: { ideaId: 'i1' }, onTargetConsumed });

    await waitFor(() => expect(api.ideas.get).toHaveBeenCalledWith('i1'));
    // Consumed on arrival, so coming back to the tab later doesn't re-navigate.
    expect(onTargetConsumed).toHaveBeenCalled();
  });

  it('says a just-recorded idea is still saving rather than gone', async () => {
    // The pane opens the instant the recording stops, which is before the
    // upload that creates the idea has landed — a 404 there is "not yet", not
    // "deleted", and the optimistic row in the list is what says which.
    vi.mocked(api.ideas.get).mockRejectedValue(new Error('Not found'));
    renderIt({ target: { ideaId: 'i1' } });

    expect(await screen.findByText(/Saving this idea/)).toBeTruthy();
  });

  it('shows nothing of the sort for a typed idea', async () => {
    renderIt();
    await openIdea();
    expect(screen.queryByText('Journal entry ›')).toBeNull();
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
    await openIdea('Decisions');
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
    await openIdea('Decisions');
    fireEvent.click(await screen.findByRole('button', { name: 'Built' }));
    await waitFor(() =>
      expect(api.ideas.update).toHaveBeenCalledWith('i1', {
        userVerdict: 'yes',
      })
    );
  });
});

describe('decisions', () => {
  const question = (over = {}) => ({
    id: 'q1',
    ideaId: 'i1',
    question: 'Where should sketches live?',
    why: 'storage',
    options: ['Paper pages', 'A new table'],
    answer: null,
    status: 'open' as const,
    answeredAt: null,
    createdAt: '2026-08-01T00:00:00Z',
    ...over,
  });

  beforeEach(() => {
    vi.mocked(api.ideas.answerQuestion).mockResolvedValue({ success: true });
  });

  it('counts the open ones on the tab, so they are visible from the Idea tab', async () => {
    vi.mocked(api.ideas.listQuestions).mockResolvedValue([question()]);
    renderIt();
    await openIdea();
    expect(
      await screen.findByRole('tab', { name: /Decisions\s*1/ })
    ).toBeTruthy();
  });

  it("offers the agent's options as a multiple choice", async () => {
    vi.mocked(api.ideas.listQuestions).mockResolvedValue([question()]);
    renderIt();
    await openIdea('Decisions');

    expect(await screen.findByText('Where should sketches live?')).toBeTruthy();
    // The options are real, selectable rows now — they used to be crammed into
    // a placeholder, which asked the user to retype what was on screen.
    expect(screen.getByRole('radio', { name: 'Paper pages' })).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'A new table' })).toBeTruthy();
  });

  it('records the chosen option verbatim', async () => {
    vi.mocked(api.ideas.listQuestions).mockResolvedValue([question()]);
    renderIt();
    await openIdea('Decisions');

    fireEvent.click(await screen.findByRole('radio', { name: 'Paper pages' }));
    fireEvent.click(screen.getByRole('button', { name: 'Decide' }));
    await waitFor(() =>
      expect(api.ideas.answerQuestion).toHaveBeenCalledWith('q1', {
        answer: 'Paper pages',
      })
    );
  });

  it('always offers a write-your-own row, last', async () => {
    vi.mocked(api.ideas.listQuestions).mockResolvedValue([question()]);
    renderIt();
    await openIdea('Decisions');

    const rows = await screen.findAllByRole('radio');
    expect(rows).toHaveLength(3);
    expect(rows[2]!.getAttribute('value')).toBe('__other__');

    fireEvent.click(rows[2]!);
    fireEvent.change(
      screen.getByLabelText('Answer: Where should sketches live?'),
      { target: { value: 'Both, keyed by page id' } }
    );
    fireEvent.click(screen.getByRole('button', { name: 'Decide' }));
    await waitFor(() =>
      expect(api.ideas.answerQuestion).toHaveBeenCalledWith('q1', {
        answer: 'Both, keyed by page id',
      })
    );
  });

  it('dictates into the write-in box rather than submitting it', async () => {
    vi.mocked(api.ideas.listQuestions).mockResolvedValue([question()]);
    renderIt();
    await openIdea('Decisions');

    fireEvent.click((await screen.findAllByRole('radio'))[2]!);
    fireEvent.click(
      screen.getByRole('button', { name: 'Dictate your answer' })
    );

    expect(await screen.findByDisplayValue('a spoken idea')).toBeTruthy();
    // A decision is committed once and read by the planner afterwards, so a
    // misheard word gets a chance to be fixed.
    expect(api.ideas.answerQuestion).not.toHaveBeenCalled();
  });

  it('degrades to the write-in row when the agent proposed nothing', async () => {
    vi.mocked(api.ideas.listQuestions).mockResolvedValue([
      question({ options: [] }),
    ]);
    renderIt();
    await openIdea('Decisions');

    const rows = await screen.findAllByRole('radio');
    expect(rows).toHaveLength(1);
    expect(rows[0]!.getAttribute('value')).toBe('__other__');
  });

  it('reopens a settled decision showing what was decided', async () => {
    vi.mocked(api.ideas.listQuestions).mockResolvedValue([
      question({
        status: 'answered',
        answer: 'A new table',
        answeredAt: '2026-08-02T00:00:00Z',
      }),
    ]);
    renderIt();
    await openIdea('Decisions');

    fireEvent.click(await screen.findByText('1 decision made'));
    const chosen = screen.getByRole('radio', {
      name: 'A new table',
    }) as HTMLInputElement;
    expect(chosen.checked).toBe(true);
    // Nothing to update until it actually changes.
    expect(
      screen.getByRole('button', { name: 'Update' }).hasAttribute('disabled')
    ).toBe(true);
  });
});

describe('plan pane', () => {
  it('offers to create a plan when there is none', async () => {
    renderIt();
    await openIdea('Plan');
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
    await openIdea('Plan');

    expect(
      await screen.findByRole('button', { name: 'Copy markdown' })
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Regenerate' })).toBeTruthy();
  });
});

describe('saving', () => {
  it('sits idle when nothing has been touched', async () => {
    renderIt();
    await openIdea();
    // The old pane compared the draft against the query cache and lost that
    // race on the first render, leaving "Saving…" up with nothing in flight.
    expect(await screen.findByLabelText('Save state: Saved')).toBeTruthy();
    await new Promise(r => setTimeout(r, 50));
    expect(api.ideas.update).not.toHaveBeenCalled();
  });

  it('says the text is local only until the server has it', async () => {
    vi.useFakeTimers();
    try {
      renderIt();
      await openIdeaWithFakeTimers();

      fireEvent.change(screen.getByLabelText('Idea body'), {
        target: { value: 'a habit grid, on the month view too' },
      });
      // Edited but not yet sent — this exists in the browser and nowhere else.
      expect(screen.getByLabelText('Save state: Unsaved')).toBeTruthy();
      expect(api.ideas.update).not.toHaveBeenCalled();

      await vi.advanceTimersByTimeAsync(1600);
      expect(api.ideas.update).toHaveBeenCalledWith('i1', {
        content: 'a habit grid, on the month view too',
      });
      await vi.advanceTimersByTimeAsync(50);
      expect(screen.getByLabelText('Save state: Saved')).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it('sends only the field that changed', async () => {
    vi.useFakeTimers();
    try {
      renderIt();
      await openIdeaWithFakeTimers();

      fireEvent.change(screen.getByLabelText('Idea title'), {
        target: { value: 'Habit grid' },
      });
      await vi.advanceTimersByTimeAsync(1600);
      expect(api.ideas.update).toHaveBeenCalledWith('i1', {
        title: 'Habit grid',
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it('can be forced rather than waited for', async () => {
    renderIt();
    await openIdea();
    fireEvent.change(screen.getByLabelText('Idea body'), {
      target: { value: 'now please' },
    });
    fireEvent.click(screen.getByLabelText('Save state: Unsaved'));
    await waitFor(() =>
      expect(api.ideas.update).toHaveBeenCalledWith('i1', {
        content: 'now please',
      })
    );
  });

  it('says so, and keeps the draft, when a save fails', async () => {
    vi.mocked(api.ideas.update).mockRejectedValue(new Error('offline'));
    renderIt();
    await openIdea();
    fireEvent.change(screen.getByLabelText('Idea body'), {
      target: { value: 'a change worth keeping' },
    });
    fireEvent.click(screen.getByLabelText('Save state: Unsaved'));

    expect(
      await screen.findByLabelText('Save state: Save failed')
    ).toBeTruthy();
    expect(screen.getByDisplayValue('a change worth keeping')).toBeTruthy();
  });

  it('does not lose an edit when another idea is opened', async () => {
    vi.mocked(api.ideas.list).mockResolvedValue([
      summary({ id: 'i1', title: 'Habit tracking' }),
      summary({ id: 'i2', title: 'Encrypted backups' }),
    ]);
    renderIt();
    await openIdea();
    fireEvent.change(screen.getByLabelText('Idea body'), {
      target: { value: 'mid-debounce' },
    });
    // Switching ideas unmounts the pane, which used to drop whatever was still
    // inside the debounce window.
    fireEvent.click(screen.getByText('Encrypted backups'));
    await waitFor(() =>
      expect(api.ideas.update).toHaveBeenCalledWith('i1', {
        content: 'mid-debounce',
      })
    );
  });

  it('changing the status does not look like an unsaved edit', async () => {
    renderIt();
    await openIdea();
    fireEvent.change(screen.getByLabelText('Status'), {
      target: { value: 'ready' },
    });
    await waitFor(() =>
      expect(api.ideas.update).toHaveBeenCalledWith('i1', { status: 'ready' })
    );
    expect(screen.getByLabelText('Save state: Saved')).toBeTruthy();
  });
});

describe('the generated title', () => {
  // Capture leaves `title` empty and names the idea seconds later
  // (backend/ai/idea_title.py), so an untitled idea is re-asked for — briefly.
  // Both the list row and the pane show the fallback in the meantime.
  const untitled = (over = {}) => summary({ id: 'i1', title: '', ...over });

  beforeEach(() => {
    vi.mocked(api.ideas.list).mockResolvedValue([untitled()]);
  });

  it('appears in the box when the background pass writes it', async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(api.ideas.get)
        .mockResolvedValueOnce(
          detail({ title: '', createdAt: new Date().toISOString() })
        )
        .mockResolvedValue(
          detail({
            title: 'Habit grid in the day view',
            createdAt: new Date().toISOString(),
          })
        );

      renderIt();
      await openIdeaWithFakeTimers('Untitled idea');
      expect(
        (screen.getByLabelText('Idea title') as HTMLInputElement).value
      ).toBe('');

      await vi.advanceTimersByTimeAsync(4500);
      await vi.advanceTimersByTimeAsync(50);
      expect(
        (screen.getByLabelText('Idea title') as HTMLInputElement).value
      ).toBe('Habit grid in the day view');
    } finally {
      vi.useRealTimers();
    }
  });

  it('never overwrites a title being typed', async () => {
    vi.useFakeTimers();
    try {
      // A real row rather than a fixed reply, because what this is testing is
      // the interaction of two writers. The server's own guard is the
      // `AND (title IS NULL OR title='')` in _enrich_idea_bg: a hand-typed
      // name is never replaced by the model's.
      let row = detail({ title: '', createdAt: new Date().toISOString() });
      vi.mocked(api.ideas.get).mockImplementation(async () => row);
      vi.mocked(api.ideas.update).mockImplementation(async (_id, data) => {
        row = { ...row, ...(data as Partial<Idea>) };
        return { success: true };
      });
      // The naming pass finishes a couple of seconds in, and finds a title.
      setTimeout(() => {
        if (!row.title) row = { ...row, title: 'Named by the model' };
      }, 2500);

      renderIt();
      await openIdeaWithFakeTimers('Untitled idea');
      fireEvent.change(screen.getByLabelText('Idea title'), {
        target: { value: 'My own name' },
      });

      await vi.advanceTimersByTimeAsync(6000);
      await vi.advanceTimersByTimeAsync(50);
      expect(
        (screen.getByLabelText('Idea title') as HTMLInputElement).value
      ).toBe('My own name');
      expect(row.title).toBe('My own name');
    } finally {
      vi.useRealTimers();
    }
  });

  it('stops asking once an idea is old enough to have been named', async () => {
    // With no AI configured the title never arrives, and a poll with no end is
    // a request every few seconds forever.
    vi.useFakeTimers();
    try {
      vi.mocked(api.ideas.get).mockResolvedValue(
        detail({ title: '', createdAt: '2026-01-01T00:00:00Z' })
      );

      renderIt();
      await openIdeaWithFakeTimers('Untitled idea');
      const calls = vi.mocked(api.ideas.get).mock.calls.length;
      expect(calls).toBeGreaterThan(0);

      await vi.advanceTimersByTimeAsync(30000);
      expect(vi.mocked(api.ideas.get).mock.calls.length).toBe(calls);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('the discussion tab', () => {
  const conversation = (over = {}) => ({
    id: 'c1',
    title: null,
    createdAt: '2026-08-01T00:00:00Z',
    updatedAt: '2026-08-01T00:00:00Z',
    ...over,
  });

  /** The SSE stream the discuss endpoint answers with. */
  function streamReply(chunks: string[]) {
    const encoder = new TextEncoder();
    const body = chunks.map(c => `data: ${c}\n\n`).concat('data: [DONE]\n\n');
    let i = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        body: {
          getReader: () => ({
            read: async () =>
              i < body.length
                ? { done: false, value: encoder.encode(body[i++]!) }
                : { done: true, value: undefined },
            releaseLock: () => undefined,
          }),
        },
      }))
    );
  }

  beforeEach(() => {
    vi.mocked(api.ideas.listConversations).mockResolvedValue([
      conversation() as never,
    ]);
    vi.mocked(api.chat.getConversation).mockResolvedValue({
      ...conversation(),
      messages: [],
    } as never);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('is a chat with its own composer rather than a section of the page', async () => {
    renderIt();
    await openIdea('Discuss');
    expect(
      await screen.findByPlaceholderText(/Ask about this idea/)
    ).toBeTruthy();
    // The Idea tab's editor is not underneath it competing for the pane.
    expect(screen.queryByLabelText('Idea body')).toBeNull();
  });

  it('has the microphone every other chat has', async () => {
    renderIt();
    await openIdea('Discuss');
    expect(
      await screen.findByRole('button', { name: 'Speak to send' })
    ).toBeTruthy();
  });

  it('sends what was dictated, the way the Chat tab does', async () => {
    streamReply([JSON.stringify({ content: 'Here is what I found.' })]);
    renderIt();
    await openIdea('Discuss');

    fireEvent.click(
      await screen.findByRole('button', { name: 'Speak to send' })
    );

    await waitFor(() =>
      expect(vi.mocked(fetch)).toHaveBeenCalledWith(
        '/api/ideas/i1/discuss',
        expect.objectContaining({ method: 'POST' })
      )
    );
    const body = JSON.parse(
      (vi.mocked(fetch).mock.calls[0]![1] as RequestInit).body as string
    );
    expect(body.message).toBe('a spoken idea');
    expect(body.conversationId).toBe('c1');
  });

  it('sends anything already typed along with the transcript', async () => {
    streamReply([JSON.stringify({ content: 'ok' })]);
    renderIt();
    await openIdea('Discuss');

    fireEvent.change(
      await screen.findByPlaceholderText(/Ask about this idea/),
      {
        target: { value: 'about the day view:' },
      }
    );
    fireEvent.click(screen.getByRole('button', { name: 'Speak to send' }));

    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
    const body = JSON.parse(
      (vi.mocked(fetch).mock.calls[0]![1] as RequestInit).body as string
    );
    expect(body.message).toBe('about the day view: a spoken idea');
  });

  it('starts a conversation on the first question', async () => {
    vi.mocked(api.ideas.listConversations).mockResolvedValue([]);
    vi.mocked(api.ideas.createConversation).mockResolvedValue({ id: 'c9' });
    streamReply([JSON.stringify({ content: 'ok' })]);
    renderIt();
    await openIdea('Discuss');

    fireEvent.change(
      await screen.findByPlaceholderText(/Ask about this idea/),
      {
        target: { value: 'has anyone built this?' },
      }
    );
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() =>
      expect(api.ideas.createConversation).toHaveBeenCalledWith('i1')
    );
    const body = JSON.parse(
      (vi.mocked(fetch).mock.calls[0]![1] as RequestInit).body as string
    );
    expect(body.conversationId).toBe('c9');
  });

  it('keeps the question when the stream fails outright', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ body: null }))
    );
    renderIt();
    await openIdea('Discuss');

    fireEvent.change(
      await screen.findByPlaceholderText(/Ask about this idea/),
      {
        target: { value: 'does this already exist?' },
      }
    );
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(
      await screen.findByDisplayValue('does this already exist?')
    ).toBeTruthy();
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
