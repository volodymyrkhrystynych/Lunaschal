// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../hooks/api';
import { Newspapers } from './Newspapers';

vi.mock('../hooks/api', () => ({
  api: {
    newspapers: {
      issues: vi.fn(),
      pressreader: vi.fn(),
      getByDate: vi.fn(),
      sync: vi.fn(),
      downloadIssue: vi.fn(),
      setAutoDownload: vi.fn(),
      uploadIssue: vi.fn(),
    },
  },
}));
vi.mock('./NewspaperReader', () => ({
  NewspaperReader: () => <div>Issue reader opened</div>,
}));

function show() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Newspapers />
    </QueryClientProvider>
  );
}

// 10am on Sep 6: inside the download window, so today's paper is Sep 6.
const TODAY = '2026-09-06';
function setClock(hour: number, date = TODAY) {
  const [y, m, d] = date.split('-').map(Number);
  vi.setSystemTime(new Date(y, m - 1, d, hour, 0));
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  setClock(10);
  vi.clearAllMocks();
  vi.mocked(api.newspapers.issues).mockResolvedValue({
    issues: [],
    archivePath: '/archive',
  });
  vi.mocked(api.newspapers.pressreader).mockResolvedValue({
    sessionSaved: true,
    autoDownload: false,
    jobs: [],
  });
  vi.mocked(api.newspapers.getByDate).mockImplementation(async date => [
    {
      paper: 'toronto-star',
      label: 'Toronto Star',
      date,
      imageUrl: '/cover.jpg',
    },
  ]);
  vi.mocked(api.newspapers.sync).mockResolvedValue([]);
  vi.mocked(api.newspapers.downloadIssue).mockResolvedValue({
    date: '2026-09-06',
    status: 'queued',
    error: '',
  });
  vi.mocked(api.newspapers.setAutoDownload).mockResolvedValue({
    sessionSaved: true,
    autoDownload: true,
    jobs: [],
  });
});

afterEach(() => {
  vi.useRealTimers();
});

it('automatically queues today once and can manually request an older date', async () => {
  const { container } = show();
  const button = await screen.findByText('Download Toronto Star issue');
  await waitFor(() =>
    expect((button as HTMLButtonElement).disabled).toBe(false)
  );
  await waitFor(() =>
    expect(api.newspapers.downloadIssue).toHaveBeenCalledTimes(1)
  );
  expect(api.newspapers.downloadIssue).toHaveBeenCalledWith(
    TODAY,
    expect.anything()
  );
  fireEvent.change(container.querySelector('input[type=date]')!, {
    target: { value: '2020-09-06' },
  });
  fireEvent.click(button);
  await waitFor(() =>
    expect(api.newspapers.downloadIssue).toHaveBeenCalledWith(
      '2020-09-06',
      expect.anything()
    )
  );
  expect(screen.queryByText('Import Toronto Star PDF')).toBeNull();
  expect(screen.queryByRole('checkbox')).toBeNull();
});

it('shows setup and disables downloads when no subscriber session is saved', async () => {
  vi.mocked(api.newspapers.pressreader).mockResolvedValue({
    sessionSaved: false,
    autoDownload: false,
    jobs: [],
  });
  show();
  await screen.findByText(/One-time subscription sign-in/);
  expect(
    (screen.getByText('Download Toronto Star issue') as HTMLButtonElement)
      .disabled
  ).toBe(true);
  expect(api.newspapers.downloadIssue).not.toHaveBeenCalled();
});

it('opens an archived issue when its Toronto Star cover is tapped', async () => {
  const date = TODAY;
  vi.mocked(api.newspapers.issues).mockResolvedValue({
    archivePath: '/archive',
    issues: [{ date, pageCount: 38, byteSize: 1000, pdfUrl: '/issue.pdf' }],
  });
  show();
  fireEvent.click(
    await screen.findByAltText(`Toronto Star front page, ${date}`)
  );
  await screen.findByText('Issue reader opened');
  expect(api.newspapers.downloadIssue).not.toHaveBeenCalled();
});

it('does not queue a second download when today is already downloading', async () => {
  vi.mocked(api.newspapers.pressreader).mockResolvedValue({
    sessionSaved: true,
    autoDownload: false,
    jobs: [
      {
        date: TODAY,
        status: 'downloading',
        error: '',
      },
    ],
  });
  show();
  await screen.findByText('Downloading issue…');
  expect(api.newspapers.downloadIssue).not.toHaveBeenCalled();
});

it('shows the previous day’s paper after midnight and does not download', async () => {
  setClock(1, '2026-09-07');
  show();
  await screen.findByText('Issue date: 2026-09-06');
  await screen.findByText('Today’s download opens at 4 am.');
  const button = screen.getByText('Download Toronto Star issue');
  expect((button as HTMLButtonElement).disabled).toBe(true);
  await waitFor(() => expect(api.newspapers.pressreader).toHaveBeenCalled());
  expect(api.newspapers.downloadIssue).not.toHaveBeenCalled();
  expect(api.newspapers.sync).not.toHaveBeenCalled();
});
