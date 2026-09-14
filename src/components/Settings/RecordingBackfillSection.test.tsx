// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RecordingBackfillSection } from './RecordingBackfillSection';
import { api } from '../../hooks/api';

vi.mock('../../hooks/api', () => ({
  api: {
    journal: {
      recordingBackfill: {
        status: vi.fn(),
        start: vi.fn(),
      },
    },
  },
}));

const statusMock = api.journal.recordingBackfill.status as ReturnType<
  typeof vi.fn
>;
const startMock = api.journal.recordingBackfill.start as ReturnType<
  typeof vi.fn
>;

function renderSection() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <RecordingBackfillSection />
    </QueryClientProvider>
  );
}

const idle = { running: false };

describe('RecordingBackfillSection', () => {
  beforeEach(() => {
    statusMock.mockReset();
    startMock.mockReset();
    startMock.mockResolvedValue({ running: true });
  });

  it('says what the backlog is before anything is pressed', async () => {
    statusMock.mockResolvedValue({
      undescribed: 106,
      untitled: 24,
      progress: idle,
    });
    renderSection();

    expect(await screen.findByText('106')).toBeTruthy();
    expect(screen.getByText('24')).toBeTruthy();
  });

  it('offers nothing to press when there is nothing to do', async () => {
    // Otherwise the button invites a pass that would walk two empty lists and
    // report success, which reads as "it did something".
    statusMock.mockResolvedValue({
      undescribed: 0,
      untitled: 0,
      progress: idle,
    });
    renderSection();

    await screen.findByText(/Nothing to catch up on/);
    expect(
      screen.getByRole('button', { name: 'Catch up on recordings' })
    ).toHaveProperty('disabled', true);
  });

  it('names the half it is on while it runs', async () => {
    // The two halves have very different rates — a CPU audio pass per clip,
    // then one GPU call per entry — so a single undifferentiated bar would look
    // stalled every time it crossed over.
    statusMock.mockResolvedValue({
      undescribed: 3,
      untitled: 2,
      progress: {
        running: true,
        phase: 'titling',
        processed: 1,
        total: 2,
        described: 3,
        titled: 1,
      },
    });
    renderSection();

    expect(await screen.findByText(/Writing titles: 1 \/ 2/)).toBeTruthy();
  });

  it('reports standing down as a pause rather than a failure', async () => {
    // The rows it did not reach are untouched and still in the backlog; the fix
    // is to resume inference and press it again, which is what it should say.
    statusMock.mockResolvedValue({
      undescribed: 40,
      untitled: 10,
      progress: {
        running: false,
        phase: 'done',
        described: 66,
        titled: 14,
        stopped: 'paused',
      },
    });
    renderSection();

    await waitFor(() =>
      expect(screen.getByText(/Stopped early \(paused\)/)).toBeTruthy()
    );
    expect(screen.getByText(/run it again to carry on/)).toBeTruthy();
    // Still pressable — that is the whole recovery path.
    expect(
      screen.getByRole('button', { name: 'Catch up on recordings' })
    ).toHaveProperty('disabled', false);
  });
});
