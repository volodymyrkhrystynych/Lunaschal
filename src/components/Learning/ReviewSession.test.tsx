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
  };
  return { DUE, GRADE, mocks };
});

vi.mock('../../hooks/api', () => ({
  api: {
    learning: mocks,
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
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

  it('shows the all-caught-up state when nothing is due', async () => {
    mocks.getDue.mockResolvedValue([]);
    renderSession();
    expect(await screen.findByText('All caught up!')).toBeTruthy();
  });

  it('flips and rates with the keyboard: D flips, S moves the rating, D commits', async () => {
    renderSession();
    await screen.findByText('What is a closure?');

    fireEvent.keyDown(window, { code: 'KeyD' }); // level 0 -> 1
    fireEvent.keyDown(window, { code: 'KeyD' }); // level 1 -> 2
    fireEvent.keyDown(window, { code: 'KeyD' }); // flip
    expect(
      await screen.findByText('A function plus its captured lexical scope.')
    ).toBeTruthy();
    expect(mocks.grade).not.toHaveBeenCalled();

    // Good (3) is highlighted by default in flip mode; S moves to Easy (4).
    expect(screen.getByText('Good').className).toContain('ring-2');
    fireEvent.keyDown(window, { code: 'KeyS' });
    expect(screen.getByText('Easy').className).toContain('ring-2');

    fireEvent.keyDown(window, { code: 'KeyD' }); // commit
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

  it('after grading, D commits the suggested rating without extra keys', async () => {
    renderSession();
    fireEvent.change(await screen.findByPlaceholderText(/Type your answer/), {
      target: { value: 'a function' },
    });
    fireEvent.click(screen.getByText('Check Answer'));
    await screen.findByText(/suggestion highlighted/);

    fireEvent.keyDown(window, { code: 'KeyD' }); // level 0 -> 1
    fireEvent.keyDown(window, { code: 'KeyD' }); // level 1 -> 2
    fireEvent.keyDown(window, { code: 'KeyD' }); // commit highlighted (suggested) rating
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
