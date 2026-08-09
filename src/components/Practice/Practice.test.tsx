// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { PracticeDrill, PracticeExplanation } from '../../hooks/api';
import { requiredMask } from '../../lib/practice';

vi.mock('../../hooks/api', async importOriginal => {
  const actual = await importOriginal<typeof import('../../hooks/api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      practice: {
        session: vi.fn(),
        submitAttempt: vi.fn(),
        gradeRecall: vi.fn(),
        stats: vi.fn(async () => ({
          totalAttempts: 0,
          avgAccuracy: null,
          avgWpm: null,
          byLanguage: {},
          recall: { attempts: 0, passes: 0, passRate: null },
        })),
        listSnippets: vi.fn(async () => []),
      },
    },
  };
});

const { api } = await import('../../hooks/api');
const { Practice } = await import('./index');

const session = api.practice.session as unknown as ReturnType<typeof vi.fn>;
const submitAttempt = api.practice.submitAttempt as unknown as ReturnType<
  typeof vi.fn
>;

function explanation(summary: string): PracticeExplanation {
  return { summary, parts: [], related: '' };
}

function speedDrill(n: number): PracticeDrill {
  return {
    id: `snippet-${n}`,
    mode: 'speed',
    language: 'javascript',
    category: 'basics',
    title: `Snippet ${n}`,
    code: `const a = ${n};`,
    explanation: explanation(`What snippet ${n} is.`),
  };
}

function blindDrill(n: number): PracticeDrill {
  return {
    id: `snippet-${n}`,
    mode: 'blind',
    language: 'javascript',
    category: 'basics',
    title: `Snippet ${n}`,
    prompt: `Write snippet ${n}.`,
  };
}

// One client for the whole file, the way App.tsx has one for the whole app:
// switching to another tab unmounts Practice but leaves the cache standing, and
// that is the situation being tested.
const client = new QueryClient({
  defaultOptions: { queries: { retry: false, gcTime: Infinity } },
});

function renderPractice() {
  return render(
    <QueryClientProvider client={client}>
      <Practice />
    </QueryClientProvider>
  );
}

// Types a drill the way the writer does now: one keystroke per position the
// drill actually asks for, with the box's own value read back between them
// because accepting one character can pull a filled-in run along behind it.
async function typeOut(drill: PracticeDrill, keystrokes?: string) {
  if (drill.mode !== 'speed') throw new Error('speed drills only');
  const box = (await screen.findByLabelText(
    `Type the ${drill.title} snippet`
  )) as HTMLTextAreaElement;
  const mask = requiredMask(drill.code);
  const chars =
    keystrokes ??
    drill.code
      .split('')
      .filter((_, i) => mask[i])
      .join('');
  for (const ch of chars) {
    fireEvent.change(box, { target: { value: box.value + ch } });
  }
  return box;
}

beforeEach(() => {
  localStorage.clear();
  client.clear();
  vi.clearAllMocks();
  submitAttempt.mockResolvedValue({
    rating: 'Good',
    progress: {
      snippetId: 'snippet-1',
      attemptsCount: 1,
      lastWpm: 40,
      lastAccuracy: 100,
      bestWpm: 40,
      bestAccuracy: 100,
      lastPracticedAt: null,
      recallAttemptsCount: 0,
      recallPasses: 0,
      lastRecallPassed: null,
      lastRecallAt: null,
    },
  });
});

describe('Practice typing', () => {
  const multiline: PracticeDrill = {
    ...(speedDrill(1) as Extract<PracticeDrill, { mode: 'speed' }>),
    code: 'if (a) {\n  const b = 1;\n}',
  };

  it('completes without the writer typing any layout', async () => {
    session.mockResolvedValueOnce([multiline]);
    renderPractice();
    await screen.findByText('Snippet 1');

    // 'if(a){const b=1;}' — every indent and line break filled in.
    await typeOut(multiline);

    await waitFor(() => expect(submitAttempt).toHaveBeenCalled());
    expect(submitAttempt.mock.calls[0][0]).toMatchObject({
      snippetId: 'snippet-1',
      accuracy: 100,
      errorCount: 0,
    });
  });

  it('scores only the characters it asked for', async () => {
    session.mockResolvedValueOnce([multiline]);
    renderPractice();
    await screen.findByText('Snippet 1');

    // One wrong character out of the 17 required, so 16/17 — not 16/25, which
    // is what counting the layout the writer never touched would have given.
    await typeOut(multiline, 'if(a){const X=1;}');

    await waitFor(() => expect(submitAttempt).toHaveBeenCalled());
    const { accuracy, errorCount } = submitAttempt.mock.calls[0][0];
    expect(errorCount).toBe(1);
    expect(accuracy).toBeCloseTo((16 / 17) * 100, 5);
  });

  it('takes a space for a line break that separates two words', async () => {
    const html: PracticeDrill = {
      ...(speedDrill(1) as Extract<PracticeDrill, { mode: 'speed' }>),
      code: '<input\n  type="text"\n/>',
    };
    session.mockResolvedValueOnce([html]);
    renderPractice();
    await screen.findByText('Snippet 1');

    await typeOut(html, '<input type="text"/>');

    await waitFor(() => expect(submitAttempt).toHaveBeenCalled());
    expect(submitAttempt.mock.calls[0][0]).toMatchObject({
      accuracy: 100,
      errorCount: 0,
    });
  });

  it('steps a backspace over the filled-in run', async () => {
    session.mockResolvedValueOnce([multiline]);
    renderPractice();
    await screen.findByText('Snippet 1');

    const box = await typeOut(multiline, 'if(a){');
    // '{' pulled the line break and indent in with it; one backspace has to
    // land on '{' rather than nibble at whitespace the writer never produced.
    expect(box.value).toBe('if (a) {\n  ');
    fireEvent.change(box, { target: { value: box.value.slice(0, -1) } });
    expect(box.value).toBe('if (a) ');
  });
});

describe('Practice', () => {
  it('keeps the explanation panel open across the next snippet', async () => {
    const first = speedDrill(1);
    const second = speedDrill(2);
    session.mockResolvedValueOnce([first]).mockResolvedValueOnce([second]);

    renderPractice();

    await screen.findByText('Snippet 1');
    fireEvent.click(screen.getByRole('button', { name: /what this is/i }));
    expect(screen.getByText('What snippet 1 is.')).toBeTruthy();

    await typeOut(first);

    await screen.findByText('Snippet 2', undefined, { timeout: 3000 });
    expect(screen.getByRole('button', { name: /what this is/i })).toBeTruthy();
    expect(screen.getByText('What snippet 2 is.')).toBeTruthy();
  });

  it('keeps the panel in place on a blind drill instead of dropping it', async () => {
    const first = speedDrill(1);
    const second = blindDrill(2);
    session.mockResolvedValueOnce([first]).mockResolvedValueOnce([second]);

    renderPractice();

    await screen.findByText('Snippet 1');
    await typeOut(first);

    await screen.findByText('Snippet 2', undefined, { timeout: 3000 });
    // Still a "What this is" row, so nothing vanished between the two drills —
    // but not a toggle, because there is nothing behind it until this is graded
    // and collapsing it would write "closed" into the shared preference.
    expect(screen.getByText(/what this is/i)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /what this is/i })).toBeNull();
  });

  it('ignores a restored drill from a build that had no explanations', async () => {
    // What the IndexedDB persister used to hand back on the next app start: the
    // cached *answer to "what do I drill next"*, serialized before per-snippet
    // explanations existed. React Query served it as the drill, `explanation`
    // came back undefined, and the panel rendered nothing — the row vanished on
    // that snippet and no refetch ever replaced it, because the queue only
    // accepts data while it is still short.
    const stale = speedDrill(1) as unknown as Record<string, unknown>;
    delete stale.explanation;
    client.setQueryData(['practice', 'next', '', '', 0], [stale]);
    session.mockResolvedValueOnce([speedDrill(2)]);

    renderPractice();

    await screen.findByText('Snippet 2', undefined, { timeout: 3000 });
    expect(screen.getByRole('button', { name: /what this is/i })).toBeTruthy();
  });

  it('serves a genuinely new snippet on the next visit to the tab', async () => {
    const first = speedDrill(1);
    const second = speedDrill(2);
    const third = speedDrill(3);
    session
      .mockResolvedValueOnce([first])
      .mockResolvedValueOnce([second])
      .mockResolvedValueOnce([third]);

    const { unmount } = renderPractice();
    await screen.findByText('Snippet 1');
    await typeOut(first);
    await screen.findByText('Snippet 2', undefined, { timeout: 3000 });
    unmount();

    // Leaving the tab and coming back starts a new session. It must not replay
    // the cached response for index 0 — that hands back a snippet already
    // drilled a moment ago instead of the one now most in need of practice.
    renderPractice();
    await screen.findByText('Snippet 3', undefined, { timeout: 3000 });
  });
});
