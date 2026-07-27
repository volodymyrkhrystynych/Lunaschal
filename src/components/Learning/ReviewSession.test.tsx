// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import {
  ShortcutProvider,
  useShortcutScope,
} from '../../shortcuts/ShortcutProvider';
import { ReviewSession } from './ReviewSession';
import type {
  GradeResult,
  LearningAttempt,
  LearningCard,
} from '../../hooks/api';

// prettier-ignore
const { CARD1, CARD2, GRADE, PENDING_ATTEMPT, GRADED_ATTEMPT, mocks } = vi.hoisted(() => {
  const CARD1: LearningCard = {
    id: 'c1',
    folderId: null,
    question: 'What is a closure?',
    answer: 'A function plus its captured lexical scope.',
    state: 'active',
    tags: [],
    sourceType: 'manual',
    sourceId: null,
    derivedFrom: null,
    revisedFrom: null,
    due: '2026-07-17T00:00:00Z',
    createdAt: '2026-07-01T00:00:00Z',
    updatedAt: '2026-07-01T00:00:00Z',
  };
  const CARD2: LearningCard = {
    ...CARD1,
    id: 'c2',
    question: 'What is hoisting?',
    answer: 'Declarations are moved to the top of their scope.',
  };
  const GRADE: GradeResult = {
    coverage: {
      claims: [
        { text: 'It is a function', essential: true, covered: true, note: '' },
        {
          text: 'It captures lexical scope',
          essential: true,
          covered: false,
          note: '',
        },
      ],
      summary: 'You got the function part but missed the captured scope.',
    },
    suggestedRating: 2,
    normalizedAnswer: 'a function',
  };
  // The persisted answer for CARD1, before and after the background grade.
  const PENDING_ATTEMPT: LearningAttempt = {
    id: 'a1',
    cardId: 'c1',
    mode: 'answered',
    answer: 'a function',
    answerMode: 'typed',
    gradeStatus: 'pending',
    coverage: null,
    suggestedRating: null,
    normalizedAnswer: null,
    createdAt: '2026-07-17T00:00:00Z',
    updatedAt: '2026-07-17T00:00:00Z',
  };
  const GRADED_ATTEMPT: LearningAttempt = {
    ...PENDING_ATTEMPT,
    gradeStatus: 'done',
    coverage: GRADE.coverage,
    suggestedRating: GRADE.suggestedRating,
    normalizedAnswer: GRADE.normalizedAnswer,
  };
  const mocks = {
    getDue: vi.fn(),
    listAttempts: vi.fn(),
    saveAttempt: vi.fn(),
    review: vi.fn(),
    verify: vi.fn(),
    verifyFollowup: vi.fn(),
    revise: vi.fn(),
    generate: vi.fn(),
    chat: vi.fn(),
    listMcpServers: vi.fn(),
  };
  return { CARD1, CARD2, GRADE, PENDING_ATTEMPT, GRADED_ATTEMPT, mocks };
});

vi.mock('../../hooks/api', () => ({
  api: {
    learning: mocks,
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
  },
}));

// In the app, Learning.tsx owns shortcut scope 1 (the mode bar); this shim
// stands in for it so D can descend to the review scope at depth 2.
function Scope1({ children }: { children: ReactNode }) {
  useShortcutScope(1, {});
  return <>{children}</>;
}

function renderSession() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return {
    // Returned so a test can stand in for the background-grade poll by
    // invalidating the attempts query on demand.
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <ShortcutProvider currentView="learning" onViewChange={() => {}}>
          <Scope1>
            <ReviewSession folderId={null} tag={null} />
          </Scope1>
        </ShortcutProvider>
      </QueryClientProvider>
    ),
  };
}

async function typeAndCheck(text: string) {
  fireEvent.change(await screen.findByPlaceholderText(/Type your answer/), {
    target: { value: text },
  });
  fireEvent.click(screen.getByText('Check Answer'));
}

// Answer card 1 with its background grade already landed: saving an attempt
// invalidates the attempts query, so the next fetch returns the graded row.
// Waits for the deck first so the mock can't be picked up by the initial
// (session-seeding) fetch, which would start the session already answered.
async function typeAndGrade(text: string) {
  await screen.findByPlaceholderText(/Type your answer/);
  mocks.listAttempts.mockResolvedValue([GRADED_ATTEMPT]);
  await typeAndCheck(text);
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  mocks.getDue.mockResolvedValue([CARD1]);
  mocks.listAttempts.mockResolvedValue([]);
  mocks.saveAttempt.mockResolvedValue({ success: true, id: 'a1' });
  mocks.review.mockResolvedValue({ due: 'later', state: 'active' });
  mocks.listMcpServers.mockResolvedValue([
    {
      id: 'srv1',
      name: 'python-docs',
      transport: 'stdio',
      command: 'npx',
      args: [],
      env: {},
      url: null,
      createdAt: '',
      updatedAt: '',
    },
  ]);
  mocks.chat.mockResolvedValue({
    reply: 'A closure *remembers* variables from where it was defined.',
    transcript: [{ role: 'system', content: 'sys' }],
    usedMcp: true,
  });
});

describe('ReviewSession', () => {
  it('checking an answer saves it for grading and advances immediately', async () => {
    mocks.getDue.mockResolvedValue([CARD1, CARD2]);
    const { queryClient } = renderSession();

    await typeAndCheck('a function');
    // Next card is shown without waiting on the grader.
    expect(await screen.findByText('What is hoisting?')).toBeTruthy();
    expect(mocks.saveAttempt).toHaveBeenCalledWith(
      expect.objectContaining({
        cardId: 'c1',
        mode: 'answered',
        answer: 'a function',
        answerMode: 'typed',
      })
    );
    expect(screen.queryByText(/captured lexical scope/)).toBeNull();

    // Finish the deck; the results pass shows card 1's back, and its grade
    // as soon as the background worker's result is polled in.
    fireEvent.click(screen.getByText('Flip'));
    expect(await screen.findByText('Result 1 of 2')).toBeTruthy();
    expect(
      screen.getByText('A function plus its captured lexical scope.')
    ).toBeTruthy();
    expect(screen.getByText('Checking your answer…')).toBeTruthy();

    mocks.listAttempts.mockResolvedValue([GRADED_ATTEMPT]);
    await queryClient.invalidateQueries({ queryKey: ['learning', 'attempts'] });
    expect(await screen.findByText(/missed the captured scope/)).toBeTruthy();
    expect(screen.getByText('It captures lexical scope')).toBeTruthy();
  });

  it('flip saves a skipped attempt without revealing the answer', async () => {
    mocks.getDue.mockResolvedValue([CARD1, CARD2]);
    renderSession();
    await screen.findByText('What is a closure?');

    fireEvent.click(screen.getByText('Flip'));
    expect(await screen.findByText('What is hoisting?')).toBeTruthy();
    expect(
      screen.queryByText('A function plus its captured lexical scope.')
    ).toBeNull();
    expect(mocks.saveAttempt).toHaveBeenCalledWith(
      expect.objectContaining({ cardId: 'c1', mode: 'skipped' })
    );
    expect(mocks.saveAttempt.mock.calls[0][0].answer).toBeUndefined();
  });

  it('resumes a session left half-answered instead of re-asking', async () => {
    // Coming back to the view: the deck still has both cards, but c1 was
    // already answered and graded before leaving.
    mocks.getDue.mockResolvedValue([CARD1, CARD2]);
    mocks.listAttempts.mockResolvedValue([GRADED_ATTEMPT]);
    renderSession();

    // Straight to the unanswered card — c1 is not asked again.
    expect(await screen.findByText('What is hoisting?')).toBeTruthy();
    expect(screen.getByText('Card 2 of 2')).toBeTruthy();
    expect(mocks.saveAttempt).not.toHaveBeenCalled();

    // ...and the restored answer and its grade are still there at the end.
    fireEvent.click(screen.getByText('Flip'));
    expect(await screen.findByText('Result 1 of 2')).toBeTruthy();
    expect(screen.getByText('a function')).toBeTruthy();
    expect(screen.getByText(/missed the captured scope/)).toBeTruthy();
    expect(screen.getByText('Hard').className).toContain('ring-2');
  });

  it('the results pass posts reviews in order: graded first, then self-rated', async () => {
    mocks.getDue.mockResolvedValue([CARD1, CARD2]);
    renderSession();

    await typeAndGrade('a function');
    await screen.findByText('What is hoisting?');
    fireEvent.click(screen.getByText('Flip'));

    // Result 1: graded — suggestion (Hard) highlighted, override with Good.
    await screen.findByText(/suggestion highlighted/);
    expect(screen.getByText('Hard').className).toContain('ring-2');
    fireEvent.click(screen.getByText('Good'));
    await waitFor(() =>
      expect(mocks.review).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({
          rating: 3,
          suggestedRating: 2,
          userAnswer: 'a function',
          answerMode: 'typed',
        })
      )
    );

    // Result 2: skipped — self-graded, no grader involvement.
    mocks.getDue.mockResolvedValue([]);
    expect(await screen.findByText('Result 2 of 2')).toBeTruthy();
    expect(
      screen.getByText('Declarations are moved to the top of their scope.')
    ).toBeTruthy();
    expect(screen.getByText(/How well did you know this/)).toBeTruthy();
    fireEvent.click(screen.getByText('Easy'));
    await waitFor(() =>
      expect(mocks.review).toHaveBeenLastCalledWith(
        'c2',
        expect.objectContaining({ rating: 4, answerMode: 'self' })
      )
    );

    // Session over and nothing due anymore.
    expect(await screen.findByText('All caught up!')).toBeTruthy();
  });

  it('shows the typed answer on the result while grading is pending', async () => {
    renderSession();

    await typeAndCheck('a function');
    expect(await screen.findByText('Result 1 of 1')).toBeTruthy();
    expect(screen.getByText('Your answer')).toBeTruthy();
    expect(screen.getByText('a function')).toBeTruthy();
    expect(screen.getByText('Checking your answer…')).toBeTruthy();

    // Rating before the grade lands still works, with the raw answer.
    fireEvent.click(screen.getByText('Good'));
    await waitFor(() =>
      expect(mocks.review).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({
          rating: 3,
          userAnswer: 'a function',
          answerMode: 'typed',
        })
      )
    );
    expect(mocks.review.mock.calls[0][1].coverage).toBeUndefined();
  });

  it('opens verification from the "card is wrong" link', async () => {
    mocks.verify.mockResolvedValue({
      status: 'noProvider',
      case: null,
      transcript: [],
    });
    renderSession();
    await typeAndGrade('a function');
    fireEvent.click(await screen.findByText('I was right — the card is wrong'));

    expect(await screen.findByText('Verify against evidence')).toBeTruthy();
    await waitFor(() => expect(mocks.verify).toHaveBeenCalledWith('c1'));
    expect(await screen.findByText(/no evidence provider bound/)).toBeTruthy();
  });

  it('renders markdown in the question and answer as formatted elements', async () => {
    mocks.getDue.mockResolvedValue([
      {
        ...CARD1,
        question: 'What does **hoisting** move?',
        answer: 'Declarations move to the top of their `scope`.',
      },
    ]);
    renderSession();

    const strong = await screen.findByText('hoisting');
    expect(strong.tagName).toBe('STRONG');
    expect(screen.queryByText(/\*\*/)).toBeNull();

    fireEvent.click(screen.getByText('Flip'));
    const code = await screen.findByText('scope');
    expect(code.tagName).toBe('CODE');
  });

  it('shows the all-caught-up state when nothing is due', async () => {
    mocks.getDue.mockResolvedValue([]);
    renderSession();
    expect(await screen.findByText('All caught up!')).toBeTruthy();
  });

  it('auto-drills to the card scope so nav keys act on the card, not app tabs', async () => {
    const onViewChange = vi.fn();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <ShortcutProvider currentView="learning" onViewChange={onViewChange}>
          <Scope1>
            <ReviewSession folderId={null} tag={null} />
          </Scope1>
        </ShortcutProvider>
      </QueryClientProvider>
    );
    await screen.findByText('What is a closure?');

    // Skip to the results pass, then move the rating with S — WITHOUT first
    // pressing D to drill in. That only works if the session already auto-drilled
    // to the card scope (depth 2); at the app-tab level (0), S would instead
    // cycle to the next view.
    fireEvent.keyDown(window, { code: 'Space' }); // skip -> results
    await screen.findByText('Result 1 of 1');
    expect(screen.getByText('Good').className).toContain('ring-2');
    fireEvent.keyDown(window, { code: 'KeyS' }); // move rating Good -> Easy

    expect(screen.getByText('Easy').className).toContain('ring-2');
    expect(onViewChange).not.toHaveBeenCalled();
  });

  it('= and - zoom the card text', async () => {
    renderSession();
    await screen.findByText('What is a closure?');
    // The card first renders at level 0, then the session drills to the card
    // scope, re-rendering the markdown; wait for that to settle before grabbing
    // the (stable) font-size container.
    let container!: HTMLElement;
    await waitFor(() => {
      container =
        screen.getByText('What is a closure?').parentElement!.parentElement!;
      expect(container.style.fontSize).toBe('20px');
    });

    fireEvent.keyDown(window, { code: 'Equal' });
    expect(container.style.fontSize).toBe('21px');
    fireEvent.keyDown(window, { code: 'Minus' });
    fireEvent.keyDown(window, { code: 'Minus' });
    expect(container.style.fontSize).toBe('19px');
  });

  it('Space skips cards, then S moves the rating and Space commits it', async () => {
    mocks.getDue.mockResolvedValue([CARD1, CARD2]);
    renderSession();
    await screen.findByText('What is a closure?');

    // S moves the rating selector only once nav has drilled into this scope;
    // Space works regardless, but we need S to work here too.
    fireEvent.keyDown(window, { code: 'KeyD' }); // level 0 -> 1
    fireEvent.keyDown(window, { code: 'KeyD' }); // level 1 -> 2
    fireEvent.keyDown(window, { code: 'Space' }); // skip card 1
    expect(await screen.findByText('What is hoisting?')).toBeTruthy();
    fireEvent.keyDown(window, { code: 'Space' }); // skip card 2

    expect(await screen.findByText('Result 1 of 2')).toBeTruthy();
    expect(
      mocks.saveAttempt.mock.calls.every(c => c[0].mode === 'skipped')
    ).toBe(true);

    // Good (3) is highlighted by default for skipped cards; S moves to Easy.
    expect(screen.getByText('Good').className).toContain('ring-2');
    fireEvent.keyDown(window, { code: 'KeyS' });
    expect(screen.getByText('Easy').className).toContain('ring-2');

    fireEvent.keyDown(window, { code: 'Space' }); // commit
    await waitFor(() =>
      expect(mocks.review).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({ rating: 4, answerMode: 'self' })
      )
    );
  });

  it('D never skips, rates, or advances during review', async () => {
    renderSession();
    await screen.findByText('What is a closure?');

    // D still drills the nav level down into the review scope...
    fireEvent.keyDown(window, { code: 'KeyD' }); // level 0 -> 1
    fireEvent.keyDown(window, { code: 'KeyD' }); // level 1 -> 2
    // ...but once there, it must not touch card state.
    fireEvent.keyDown(window, { code: 'KeyD' });
    expect(screen.getByText('Card 1 of 1')).toBeTruthy();
    expect(
      screen.queryByText('A function plus its captured lexical scope.')
    ).toBeNull();

    fireEvent.keyDown(window, { code: 'Space' }); // skip -> results
    await screen.findByText('A function plus its captured lexical scope.');
    fireEvent.keyDown(window, { code: 'KeyD' });
    expect(mocks.review).not.toHaveBeenCalled();
  });

  it('a digit commits that rating directly on the results pass', async () => {
    renderSession();
    await screen.findByText('What is a closure?');

    fireEvent.keyDown(window, { code: 'Space' }); // skip -> results
    expect(
      await screen.findByText('A function plus its captured lexical scope.')
    ).toBeTruthy();

    fireEvent.keyDown(window, { code: 'Digit4' });
    await waitFor(() =>
      expect(mocks.review).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({ rating: 4, answerMode: 'self' })
      )
    );
  });

  it('digits are inert while answering', async () => {
    mocks.getDue.mockResolvedValue([CARD1, CARD2]);
    renderSession();
    await screen.findByText('What is a closure?');

    fireEvent.keyDown(window, { code: 'Digit2' });

    expect(mocks.review).not.toHaveBeenCalled();
    expect(screen.getByText('Card 1 of 2')).toBeTruthy();
  });

  it('Enter submits the typed answer and advances', async () => {
    mocks.getDue.mockResolvedValue([CARD1, CARD2]);
    renderSession();
    fireEvent.change(await screen.findByPlaceholderText(/Type your answer/), {
      target: { value: 'a function' },
    });

    fireEvent.keyDown(window, { code: 'Enter' });
    await waitFor(() =>
      expect(mocks.saveAttempt).toHaveBeenCalledWith(
        expect.objectContaining({
          cardId: 'c1',
          mode: 'answered',
          answer: 'a function',
          answerMode: 'typed',
        })
      )
    );
    expect(await screen.findByText('What is hoisting?')).toBeTruthy();
  });

  it('Enter does nothing when the answer box is empty', async () => {
    renderSession();
    await screen.findByText('What is a closure?');

    fireEvent.keyDown(window, { code: 'Enter' });

    expect(mocks.saveAttempt).not.toHaveBeenCalled();
    expect(screen.getByText('Card 1 of 1')).toBeTruthy();
  });

  it('after grading, Space commits the suggested rating without extra keys', async () => {
    renderSession();
    await typeAndGrade('a function');
    await screen.findByText(/suggestion highlighted/);

    fireEvent.keyDown(window, { code: 'Space' }); // commit highlighted (suggested) rating
    await waitFor(() =>
      expect(mocks.review).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({
          rating: 2,
          suggestedRating: 2,
          answerMode: 'typed',
        })
      )
    );
  });

  describe('discuss chat', () => {
    async function openChatAfterGrading() {
      renderSession();
      await typeAndGrade('a function');
      await screen.findByText(/suggestion highlighted/);
      fireEvent.click(screen.getByText('💬 Discuss this card'));
      return screen.findByPlaceholderText(/Ask for clarification/);
    }

    it('sends a first message with the graded answer attached', async () => {
      const input = await openChatAfterGrading();
      fireEvent.change(input, { target: { value: 'Why does it capture?' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      await waitFor(() =>
        expect(mocks.chat).toHaveBeenCalledWith('c1', {
          message: 'Why does it capture?',
          transcript: undefined,
          mcpServerId: undefined,
          userAnswer: 'a function',
        })
      );
      // The user's message and the markdown-rendered reply both appear.
      expect(await screen.findByText('Why does it capture?')).toBeTruthy();
      const em = await screen.findByText('remembers');
      expect(em.tagName).toBe('EM');
    });

    it('round-trips the transcript on follow-ups without re-sending the answer', async () => {
      const input = await openChatAfterGrading();
      fireEvent.change(input, { target: { value: 'first' } });
      fireEvent.keyDown(input, { key: 'Enter' });
      await screen.findByText('remembers');

      fireEvent.change(input, { target: { value: 'second' } });
      fireEvent.keyDown(input, { key: 'Enter' });
      await waitFor(() =>
        expect(mocks.chat).toHaveBeenLastCalledWith('c1', {
          message: 'second',
          transcript: [{ role: 'system', content: 'sys' }],
          mcpServerId: undefined,
          userAnswer: undefined,
        })
      );
    });

    it('the source picker can force a specific server or model-only', async () => {
      const input = await openChatAfterGrading();
      const picker = screen.getByTitle(/Knowledge source/);
      expect(await screen.findByText('python-docs')).toBeTruthy();

      fireEvent.change(picker, { target: { value: 'srv1' } });
      fireEvent.change(input, { target: { value: 'from docs' } });
      fireEvent.keyDown(input, { key: 'Enter' });
      await waitFor(() =>
        expect(mocks.chat).toHaveBeenLastCalledWith(
          'c1',
          expect.objectContaining({ mcpServerId: 'srv1' })
        )
      );

      fireEvent.change(picker, { target: { value: 'none' } });
      fireEvent.change(input, { target: { value: 'no source' } });
      fireEvent.keyDown(input, { key: 'Enter' });
      await waitFor(() =>
        expect(mocks.chat).toHaveBeenLastCalledWith(
          'c1',
          expect.objectContaining({ mcpServerId: null })
        )
      );
    });

    it('is available on skipped cards without a graded answer', async () => {
      renderSession();
      await screen.findByText('What is a closure?');
      fireEvent.click(screen.getByText('Flip'));
      fireEvent.click(await screen.findByText('💬 Discuss this card'));
      const input = await screen.findByPlaceholderText(/Ask for clarification/);
      fireEvent.change(input, { target: { value: 'clarify' } });
      fireEvent.keyDown(input, { key: 'Enter' });
      await waitFor(() =>
        expect(mocks.chat).toHaveBeenCalledWith(
          'c1',
          expect.objectContaining({ userAnswer: undefined })
        )
      );
    });

    it('typing digits in the chat input does not rate the card', async () => {
      const input = await openChatAfterGrading();
      fireEvent.keyDown(input, { code: 'Digit1' });
      expect(mocks.review).not.toHaveBeenCalled();
    });
  });
});
