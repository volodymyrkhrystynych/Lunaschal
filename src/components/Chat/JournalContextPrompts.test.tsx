// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type JournalEntry } from '../../hooks/api';
import { useClipStage, type ClipStage } from '../../hooks/useClipStage';
import { JournalContextPrompts } from './JournalContextPrompts';

vi.mock('../../hooks/api', () => ({
  api: {
    journal: {
      contextPrompts: vi.fn(),
      resolveContextPrompt: vi.fn(),
    },
  },
}));
vi.mock('../../hooks/useClipStage', () => ({ useClipStage: vi.fn() }));

const entry: JournalEntry = {
  id: 'entry-one',
  content: '',
  rawContent: null,
  title: 'Game night',
  tags: null,
  curatedTags: [],
  createdAt: '2026-10-13T20:00:00Z',
  attachments: [
    {
      id: 'image-one',
      entryId: 'entry-one',
      kind: 'image',
      name: 'Victory screen',
      url: '/image',
      position: 0,
      transcript: 'AI caption',
      transcriptStatus: 'done',
    },
    {
      id: 'audio-one',
      entryId: 'entry-one',
      kind: 'audio',
      name: 'Game audio',
      url: '/audio',
      position: 1,
      transcript: null,
      transcriptStatus: 'idle',
    },
  ] as JournalEntry['attachments'],
};
let stage: ClipStage;
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.journal.contextPrompts).mockResolvedValue([entry]);
  vi.mocked(api.journal.resolveContextPrompt).mockResolvedValue({
    success: true,
  });
  stage = {
    clips: [],
    status: 'idle',
    recording: false,
    starting: false,
    busy: false,
    error: '',
    notice: '',
    toggle: vi.fn(),
    remove: vi.fn(),
    claimId: vi.fn(),
    commit: vi.fn().mockResolvedValue(undefined),
    reset: vi.fn(),
  };
  vi.mocked(useClipStage).mockImplementation(() => stage);
});

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <JournalContextPrompts />
    </QueryClientProvider>
  );
}

it('shows original media and saves typed context into that entry', async () => {
  mount();
  fireEvent.click(await screen.findByText('Add context'));
  expect(screen.getByAltText('Victory screen').getAttribute('src')).toBe(
    '/image'
  );
  expect(screen.getByLabelText('Game audio').getAttribute('src')).toBe(
    '/audio'
  );
  fireEvent.change(screen.getByLabelText('What was happening?'), {
    target: { value: '  Won with friends  ' },
  });
  fireEvent.click(screen.getByText('Save context'));
  await waitFor(() =>
    expect(api.journal.resolveContextPrompt).toHaveBeenCalledWith(
      'entry-one',
      'save',
      'Won with friends'
    )
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
});

it('sends recorded context through the existing clip stage for the original entry', async () => {
  stage.clips = [{ id: 'clip' }] as ClipStage['clips'];
  mount();
  fireEvent.click(await screen.findByText('Add context'));
  expect(useClipStage).toHaveBeenCalledWith({
    kind: 'journal',
    entryId: 'entry-one',
  });
  fireEvent.click(screen.getByText('Save context'));
  await waitFor(() => expect(stage.commit).toHaveBeenCalledTimes(1));
  expect(api.journal.resolveContextPrompt).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
});

it('keeps text and staged audio when saving fails', async () => {
  vi.mocked(api.journal.resolveContextPrompt).mockRejectedValue(
    new Error('Could not save')
  );
  stage.clips = [{ id: 'clip' }] as ClipStage['clips'];
  mount();
  fireEvent.click(await screen.findByText('Add context'));
  fireEvent.change(screen.getByLabelText('What was happening?'), {
    target: { value: 'My context' },
  });
  fireEvent.click(screen.getByText('Save context'));
  expect((await screen.findByRole('alert')).textContent).toBe('Could not save');
  expect(
    (screen.getByLabelText('What was happening?') as HTMLTextAreaElement).value
  ).toBe('My context');
  expect(stage.commit).not.toHaveBeenCalled();
  expect(stage.reset).not.toHaveBeenCalled();
});

it('dismisses permanently and closing alone does not dismiss', async () => {
  mount();
  fireEvent.click(await screen.findByText('Add context'));
  fireEvent.click(screen.getByText('Close'));
  expect(api.journal.resolveContextPrompt).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('Dismiss permanently'));
  await waitFor(() =>
    expect(api.journal.resolveContextPrompt).toHaveBeenCalledWith(
      'entry-one',
      'dismiss'
    )
  );
});

it('cannot close a modal while its recording is being captured', async () => {
  stage.recording = true;
  stage.status = 'recording';
  mount();
  fireEvent.click(await screen.findByText('Add context'));
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(screen.getByRole('dialog')).toBeTruthy();
  expect((screen.getByText('Close') as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByText('Stop'));
  expect(stage.toggle).toHaveBeenCalled();
});
