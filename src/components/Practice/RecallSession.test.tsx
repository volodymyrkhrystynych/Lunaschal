// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent, screen } from '@testing-library/react';
import type { PracticeBlindDrill, PracticeRecallResult } from '../../hooks/api';
import { RecallSession } from './RecallSession';

// CodeMirror only mounts once a grade has come back (to show the reference
// answer), and it does not render in jsdom. Stubbing it keeps these tests on
// the thing under test: what the blind drill shows before and after grading.
vi.mock('./TypingCanvas', () => ({
  TypingCanvas: ({ code }: { code: string }) => (
    <pre data-testid="reference">{code}</pre>
  ),
}));

const drill: PracticeBlindDrill = {
  id: 'react-usestate',
  language: 'react',
  category: 'hooks',
  title: 'useState',
  mode: 'blind',
  prompt: 'Declare a state variable `count` starting at 0, with its setter.',
};

const graded = (
  overrides: Partial<PracticeRecallResult> = {}
): PracticeRecallResult =>
  ({
    verdict: 'correct',
    passed: true,
    feedback: 'Exactly right.',
    gradedBy: 'model',
    reference: 'const [count, setCount] = useState(0);',
    progress: null,
    ...overrides,
  }) as unknown as PracticeRecallResult;

function renderDrill(props: Partial<Parameters<typeof RecallSession>[0]> = {}) {
  const onSubmit = vi.fn();
  const onNext = vi.fn();
  const utils = render(
    <RecallSession
      snippet={drill}
      result={null}
      grading={false}
      error={false}
      onSubmit={onSubmit}
      onNext={onNext}
      {...props}
    />
  );
  return { ...utils, onSubmit, onNext };
}

describe('RecallSession', () => {
  it('shows the prompt and never the answer before grading', () => {
    renderDrill();
    expect(screen.getByText(drill.prompt)).toBeTruthy();
    // The point of the drill: the reference is not on the page to be copied.
    expect(screen.queryByTestId('reference')).toBeNull();
    expect(document.body.textContent).not.toContain('useState(0)');
  });

  it('will not submit an empty answer', () => {
    const { onSubmit } = renderDrill();
    const button = screen.getByRole('button', { name: 'Check' });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('submits what was written', () => {
    const { onSubmit } = renderDrill();
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'const [count, setCount] = useState(0);' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Check' }));
    expect(onSubmit).toHaveBeenCalledWith(
      'const [count, setCount] = useState(0);'
    );
  });

  it('takes a newline on plain Enter and submits on Ctrl+Enter', () => {
    const { onSubmit } = renderDrill();
    const box = screen.getByRole('textbox');
    fireEvent.change(box, { target: { value: 'useEffect(() => {' } });
    // Snippets are multi-line, so Enter has to stay a newline.
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(box, { key: 'Enter', ctrlKey: true });
    expect(onSubmit).toHaveBeenCalledWith('useEffect(() => {');
  });

  it('reveals the reference and the feedback once graded', () => {
    renderDrill({ result: graded() });
    expect(screen.getByText('Correct')).toBeTruthy();
    expect(screen.getByText('Exactly right.')).toBeTruthy();
    expect(screen.getByTestId('reference').textContent).toBe(
      'const [count, setCount] = useState(0);'
    );
  });

  it('waits for a button rather than auto-advancing', () => {
    // The feedback and the reference answer are the payoff of the drill; a
    // timed flash would pull them away mid-read.
    const { onNext } = renderDrill({ result: graded() });
    expect(onNext).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(onNext).toHaveBeenCalled();
  });

  it('says out loud when the verdict came from an offline text comparison', () => {
    renderDrill({
      result: graded({
        verdict: 'wrong',
        passed: false,
        gradedBy: 'fallback',
        feedback: 'Graded offline by exact text comparison.',
      }),
    });
    expect(screen.getByText(/graded offline by text comparison/i)).toBeTruthy();
  });

  it('does not claim an offline grade when the model did the reading', () => {
    renderDrill({ result: graded() });
    expect(screen.queryByText(/graded offline/i)).toBeNull();
  });

  it('reports a grading failure without recording anything', () => {
    renderDrill({ error: true });
    expect(screen.getByText(/nothing has been recorded/i)).toBeTruthy();
  });
});
