// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import {
  ShortcutProvider,
  useShortcutScope,
} from '../../shortcuts/ShortcutProvider';
import {
  LEARNING_CARD_FONT_SIZE_STEP,
  getStoredLearningCardFontSize,
  setStoredLearningCardFontSize,
} from '../../lib/fontSize';
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
    settings: { get: vi.fn().mockResolvedValue({}) },
  },
}));

const { playCompletionChime } = vi.hoisted(() => ({
  playCompletionChime: vi.fn(),
}));
vi.mock('../../lib/sound', () => ({ playCompletionChime }));

// In the app, Learning.tsx owns shortcut scope 1 (the mode bar) — including
// the card font-size shortcuts — plus the scrollable container ref. This
// shim mirrors both so D can descend to the review scope at depth 2, and
// so =/- and W/S can be exercised against a real DOM node.
function LearningShim() {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [fontSize, setFontSize] = useState(getStoredLearningCardFontSize);

  useShortcutScope(1, {
    fontUp: () =>
      setFontSize(px =>
        setStoredLearningCardFontSize(px + LEARNING_CARD_FONT_SIZE_STEP)
      ),
    fontDown: () =>
      setFontSize(px =>
        setStoredLearningCardFontSize(px - LEARNING_CARD_FONT_SIZE_STEP)
      ),
  });

  return (
    <div ref={scrollRef}>
      <ReviewSession
        folderId={null}
        tag={null}
        scrollRef={scrollRef}
        fontSize={fontSize}
      />
    </div>
  );
}

function renderSession() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="learning" onViewChange={() => {}}>
        <LearningShim />
      </ShortcutProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
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
    expect(playCompletionChime).toHaveBeenCalledTimes(1);
  });

  it('does not chime on a plain flip — only a graded assessment does', async () => {
    renderSession();
    fireEvent.click(await screen.findByText('Flip'));
    expect(
      screen.getByText('A function plus its captured lexical scope.')
    ).toBeTruthy();
    expect(playCompletionChime).not.toHaveBeenCalled();
  });

  it('scales the coverage assessment along with the card zoom level', async () => {
    renderSession();
    await screen.findByText('What is a closure?');
    fireEvent.keyDown(window, { code: 'Equal' }); // zoom in one step: 20 -> 21

    fireEvent.change(await screen.findByPlaceholderText(/Type your answer/), {
      target: { value: 'a function' },
    });
    fireEvent.click(screen.getByText('Check Answer'));

    const summary = await screen.findByText(/missed the captured scope/);
    // 14px base * (21/20) zoom ratio, same ratio driving the question/answer text.
    expect(summary.parentElement!.style.fontSize).toBe('14.7px');
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

  it('fenced code blocks scale relative to the card, not a fixed root size', async () => {
    mocks.getDue.mockResolvedValue([
      {
        ...DUE[0],
        question: 'What does this print?\n\n```js\nconsole.log(1);\n```',
      },
    ]);
    renderSession();

    const codeText = await screen.findByText('console.log(1);');
    const pre = codeText.closest('pre')!;
    // Relative (em) sizing, not a fixed rem class — so it scales with the
    // ancestor's font-size instead of staying pinned to the root size.
    expect(pre.className).toContain('text-[0.75em]');
    expect(pre.className).not.toContain('text-xs');
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

  it('D only navigates: it descends to the card level but does not flip or commit', async () => {
    renderSession();
    await screen.findByText('What is a closure?');

    fireEvent.keyDown(window, { code: 'KeyD' }); // level 0 -> 1
    fireEvent.keyDown(window, { code: 'KeyD' }); // level 1 -> 2
    // Already on the card now — D is a no-op here, not a flip.
    fireEvent.keyDown(window, { code: 'KeyD' });
    expect(
      screen.queryByText('A function plus its captured lexical scope.')
    ).toBeNull();
    expect(mocks.grade).not.toHaveBeenCalled();
    expect(mocks.review).not.toHaveBeenCalled();

    // Space still flips...
    fireEvent.keyDown(window, { code: 'Space' });
    expect(
      await screen.findByText('A function plus its captured lexical scope.')
    ).toBeTruthy();

    // ...and D remains a no-op — it doesn't commit the highlighted rating either.
    fireEvent.keyDown(window, { code: 'KeyD' });
    expect(mocks.review).not.toHaveBeenCalled();

    fireEvent.keyDown(window, { code: 'Digit4' }); // digits still commit
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

  it('=/- grow and shrink the card text and persist the size, matching the library shortcuts', async () => {
    const { container } = renderSession();
    await screen.findByText('What is a closure?');
    const questionBox = () =>
      container.querySelector<HTMLElement>('.leading-relaxed')!;
    // The card wrapper (mx-auto) sits three levels up from the question box.
    const cardWrapper = () =>
      questionBox().parentElement!.parentElement!.parentElement!;
    expect(questionBox().style.fontSize).toBe('20px');
    expect(cardWrapper().style.maxWidth).toBe('min(576px, 100%)');

    fireEvent.keyDown(window, { code: 'Equal' });
    expect(questionBox().style.fontSize).toBe('21px');
    expect(localStorage.getItem('lunaschal:learningCardFontSize')).toBe('21');
    // The card widens along with the text, so code blocks get more room
    // before they need to scroll instead of scrolling immediately.
    expect(cardWrapper().style.maxWidth).toBe('min(604.8px, 100%)');

    fireEvent.keyDown(window, { code: 'Minus' });
    fireEvent.keyDown(window, { code: 'Minus' });
    expect(questionBox().style.fontSize).toBe('19px');
    expect(localStorage.getItem('lunaschal:learningCardFontSize')).toBe('19');
    expect(cardWrapper().style.maxWidth).toBe('min(547.2px, 100%)');
  });

  it('W/S scroll the review container instead of changing the rating selection', async () => {
    Element.prototype.scrollBy = vi.fn();
    renderSession();
    await screen.findByText('What is a closure?');

    fireEvent.keyDown(window, { code: 'KeyD' }); // level 0 -> 1
    fireEvent.keyDown(window, { code: 'KeyD' }); // level 1 -> 2
    fireEvent.keyDown(window, { code: 'Space' }); // flip
    await screen.findByText('A function plus its captured lexical scope.');

    // Good (3) is highlighted by default and stays put — 1-4 rate directly now.
    expect(screen.getByText('Good').className).toContain('ring-2');

    const scrollSpy = Element.prototype.scrollBy as unknown as ReturnType<
      typeof vi.fn
    >;
    fireEvent.keyDown(window, { code: 'KeyS' });
    expect(scrollSpy).toHaveBeenCalledWith({ top: 120, behavior: 'smooth' });
    expect(screen.getByText('Good').className).toContain('ring-2');

    fireEvent.keyDown(window, { code: 'KeyW' });
    expect(scrollSpy).toHaveBeenCalledWith({ top: -120, behavior: 'smooth' });
    expect(screen.getByText('Good').className).toContain('ring-2');
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

  it('after grading, D does not commit the suggested rating — a digit is required', async () => {
    renderSession();
    fireEvent.change(await screen.findByPlaceholderText(/Type your answer/), {
      target: { value: 'a function' },
    });
    fireEvent.click(screen.getByText('Check Answer'));
    await screen.findByText(/suggestion highlighted/);

    fireEvent.keyDown(window, { code: 'KeyD' }); // level 0 -> 1
    fireEvent.keyDown(window, { code: 'KeyD' }); // level 1 -> 2
    fireEvent.keyDown(window, { code: 'KeyD' }); // no-op on the card
    expect(mocks.review).not.toHaveBeenCalled();

    fireEvent.keyDown(window, { code: 'Digit2' }); // suggested (Hard) rating
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
