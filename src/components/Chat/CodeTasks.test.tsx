// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type CodeTask, type CodeTaskEvent } from '@/hooks/api';
import { CodeTasks } from './CodeTasks';

vi.mock('@/hooks/api', () => ({
  api: { code: { list: vi.fn(), get: vi.fn(), diff: vi.fn() } },
}));
vi.mock('@/hooks/useRecorder', () => ({
  useRecorder: () => ({ status: 'idle', starting: false, error: null }),
}));

const task = (status: string, extra: Partial<CodeTask> = {}): CodeTask => ({
  id: 'T1',
  repoId: 'R',
  title: 'Voice to-do',
  prompt: 'p',
  harness: 'pi',
  status,
  phase: 'build',
  plan: '1.',
  summary: null,
  diffStat: null,
  headSha: 'abc',
  testExit: 1,
  testOutput: 'What failed:\nFAILED t.py::x',
  prUrl: null,
  error: null,
  createdAt: null,
  updatedAt: null,
  pending: null,
  running: true,
  ...extra,
});

const events: CodeTaskEvent[] = [
  { seq: 1, kind: 'test', payload: { exit: 1, tail: 'old' }, createdAt: null },
  {
    seq: 2,
    kind: 'status',
    payload: { status: 'running' },
    createdAt: new Date(Date.now() - 125_000).toISOString(),
  },
  {
    seq: 3,
    kind: 'tool_call',
    payload: { title: 'npm run -s test:all' },
    createdAt: null,
  },
];

function open(t: CodeTask) {
  vi.mocked(api.code.list).mockResolvedValue({ tasks: [t] } as never);
  vi.mocked(api.code.get).mockResolvedValue({ task: t, events } as never);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <CodeTasks />
    </QueryClientProvider>
  );
}

beforeEach(() => vi.clearAllMocks());

it('says the agent is working, and hides the previous verdict, while a follow-up runs', async () => {
  open(task('running'));
  // The list says it is working and does not repeat the old failure.
  const row = await screen.findByText('Voice to-do');
  expect(screen.queryByText(/tests failed/)).toBeNull();
  fireEvent.click(row);
  const banner = await screen.findByTestId('code-activity');
  expect(banner.textContent).toContain('The agent is working');
  expect(banner.textContent).toMatch(/2m 0\ds/);
  expect(banner.textContent).toContain('Running: npm run -s test:all');
  expect(screen.queryByText(/✗ Tests failed/)).toBeNull();
});

it('shows the verdict and no banner once the task is done', async () => {
  open(task('done', { running: false }));
  fireEvent.click(await screen.findByText('Voice to-do'));
  await waitFor(() =>
    expect(screen.getByText('✗ Tests failed (exit 1)')).toBeTruthy()
  );
  expect(screen.queryByTestId('code-activity')).toBeNull();
});
