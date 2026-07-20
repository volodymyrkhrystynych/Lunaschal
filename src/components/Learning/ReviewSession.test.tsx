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
import type { GradeResult, LearningCard } from '../../hooks/api';

const { DUE, GRADE, mocks } = vi.hoisted(() => {
  const DUE: LearningCard[] = [
    {
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
    },
  ];
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
  const mocks = {
    getDue: vi.fn(),
    grade: vi.fn(),
    review: vi.fn(),
    verify: vi.fn(),
    verifyFollowup: vi.fn(),
    revise: vi.fn(),
    generate: vi.fn(),
    chat: vi.fn(),
    listMcpServers: vi.fn(),
  };
  return { DUE, GRADE, mocks };
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
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="learning" onViewChange={() => {}}>
        <Scope1>
          <ReviewSession folderId={null} tag={null} />
        </Scope1>
      </ShortcutProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getDue.mockResolvedValue(DUE);
  mocks.grade.mockResolvedValue(GRADE);
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
  it('grades a typed answer and shows the coverage breakdown', async () => {
    renderSession();
    fireEvent.change(await screen.findByPlaceholderText(/Type your answer/), {
      target: { value: 'a function' },
    });
    fireEvent.click(screen.getByText('Check Answer'));

    await waitFor(() =>
      expect(mocks.grade).toHaveBeenCalledWith('c1', {
        answer: 'a function',
        answerMode: 'typed',
      })
    );
    expect(await screen.findByText(/missed the captured scope/)).toBeTruthy();
    expect(screen.getByText('It captures lexical scope')).toBeTruthy();
  });

  it('pre-selects the suggested rating and lets the user override', async () => {
    renderSession();
    fireEvent.change(await screen.findByPlaceholderText(/Type your answer/), {
      target: { value: 'a function' },
    });
    fireEvent.click(screen.getByText('Check Answer'));
    await screen.findByText(/suggestion highlighted/);

    // Hard (2) is the suggestion → highlighted with a ring.
    expect(screen.getByText('Hard').className).toContain('ring-2');
    expect(screen.getByText('Good').className).not.toContain('ring-2');

    // Overriding: tapping Good posts rating 3 with the suggestion recorded.
    fireEvent.click(screen.getByText('Good'));
    await waitFor(() =>
      expect(mocks.review).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({
          rating: 3,
          suggestedRating: 2,
          answerMode: 'typed',
        })
      )
    );
  });

  it('flip mode self-grades without calling the grader', async () => {
    renderSession();
    fireEvent.click(await screen.findByText('Flip'));
    expect(
      screen.getByText('A function plus its captured lexical scope.')
    ).toBeTruthy();
    fireEvent.click(screen.getByText('Easy'));
    await waitFor(() =>
      expect(mocks.review).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({
          rating: 4,
          answerMode: 'self',
        })
      )
    );
    expect(mocks.grade).not.toHaveBeenCalled();
  });

  it('opens verification from the "card is wrong" link', async () => {
    mocks.verify.mockResolvedValue({
      status: 'noProvider',
      case: null,
      transcript: [],
    });
    renderSession();
    fireEvent.change(await screen.findByPlaceholderText(/Type your answer/), {
      target: { value: 'a function' },
    });
    fireEvent.click(screen.getByText('Check Answer'));
    fireEvent.click(await screen.findByText('I was right — the card is wrong'));

    expect(await screen.findByText('Verify against evidence')).toBeTruthy();
    await waitFor(() => expect(mocks.verify).toHaveBeenCalledWith('c1'));
    expect(await screen.findByText(/no evidence provider bound/)).toBeTruthy();
  });

  it('renders markdown in the question and answer as formatted elements', async () => {
    mocks.getDue.mockResolvedValue([
      {
        ...DUE[0],
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

  it('flips and rates with the keyboard: Space flips, S moves the rating, Space commits', async () => {
    renderSession();
    await screen.findByText('What is a closure?');

    // S moves the rating selector only once nav has drilled into this scope;
    // Space (flip/commit) works regardless, but we need S to work here too.
    fireEvent.keyDown(window, { code: 'KeyD' }); // level 0 -> 1
    fireEvent.keyDown(window, { code: 'KeyD' }); // level 1 -> 2
    fireEvent.keyDown(window, { code: 'Space' }); // flip
    expect(
      await screen.findByText('A function plus its captured lexical scope.')
    ).toBeTruthy();
    expect(mocks.grade).not.toHaveBeenCalled();

    // Good (3) is highlighted by default in flip mode; S moves to Easy (4).
    expect(screen.getByText('Good').className).toContain('ring-2');
    fireEvent.keyDown(window, { code: 'KeyS' });
    expect(screen.getByText('Easy').className).toContain('ring-2');

    fireEvent.keyDown(window, { code: 'Space' }); // commit
    await waitFor(() =>
      expect(mocks.review).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({
          rating: 4,
          answerMode: 'self',
        })
      )
    );
  });

  it('D never flips, commits, or advances the card during review', async () => {
    renderSession();
    await screen.findByText('What is a closure?');

    // D still drills the nav level down into the review scope...
    fireEvent.keyDown(window, { code: 'KeyD' }); // level 0 -> 1
    fireEvent.keyDown(window, { code: 'KeyD' }); // level 1 -> 2
    // ...but once there, it must not touch card state.
    fireEvent.keyDown(window, { code: 'KeyD' });
    expect(
      screen.queryByText('A function plus its captured lexical scope.')
    ).toBeNull();
    expect(mocks.review).not.toHaveBeenCalled();

    fireEvent.keyDown(window, { code: 'Space' });
    await screen.findByText('A function plus its captured lexical scope.');
    fireEvent.keyDown(window, { code: 'KeyD' });
    expect(mocks.review).not.toHaveBeenCalled();
  });

  it('Space flips the card and a digit commits that rating directly', async () => {
    renderSession();
    await screen.findByText('What is a closure?');

    fireEvent.keyDown(window, { code: 'Space' });
    expect(
      await screen.findByText('A function plus its captured lexical scope.')
    ).toBeTruthy();
    expect(mocks.grade).not.toHaveBeenCalled();

    fireEvent.keyDown(window, { code: 'Digit4' });
    await waitFor(() =>
      expect(mocks.review).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({
          rating: 4,
          answerMode: 'self',
        })
      )
    );
  });

  describe('discuss chat', () => {
    async function openChatAfterGrading() {
      renderSession();
      fireEvent.change(await screen.findByPlaceholderText(/Type your answer/), {
        target: { value: 'a function' },
      });
      fireEvent.click(screen.getByText('Check Answer'));
      fireEvent.click(await screen.findByText('💬 Discuss this card'));
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

    it('is available in flip mode without a graded answer', async () => {
      renderSession();
      fireEvent.click(await screen.findByText('Flip'));
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

  it('digits are inert before the answer is shown', async () => {
    renderSession();
    await screen.findByText('What is a closure?');

    fireEvent.keyDown(window, { code: 'Digit2' });

    expect(mocks.review).not.toHaveBeenCalled();
  });

  it('Enter checks the typed answer, then a digit overrides the suggestion', async () => {
    renderSession();
    fireEvent.change(await screen.findByPlaceholderText(/Type your answer/), {
      target: { value: 'a function' },
    });

    fireEvent.keyDown(window, { code: 'Enter' });
    await waitFor(() =>
      expect(mocks.grade).toHaveBeenCalledWith('c1', {
        answer: 'a function',
        answerMode: 'typed',
      })
    );
    await screen.findByText(/suggestion highlighted/);

    fireEvent.keyDown(window, { code: 'Digit1' });
    await waitFor(() =>
      expect(mocks.review).toHaveBeenCalledWith(
        'c1',
        expect.objectContaining({
          rating: 1,
          suggestedRating: 2,
          answerMode: 'typed',
        })
      )
    );
  });

  it('Enter does nothing when the answer box is empty', async () => {
    renderSession();
    await screen.findByText('What is a closure?');

    fireEvent.keyDown(window, { code: 'Enter' });

    expect(mocks.grade).not.toHaveBeenCalled();
  });

  it('after grading, Space commits the suggested rating without extra keys', async () => {
    renderSession();
    fireEvent.change(await screen.findByPlaceholderText(/Type your answer/), {
      target: { value: 'a function' },
    });
    fireEvent.click(screen.getByText('Check Answer'));
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
});
