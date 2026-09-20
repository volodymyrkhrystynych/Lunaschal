// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Journal } from './Journal';
import { ShortcutProvider } from '../shortcuts/ShortcutProvider';
import { api } from '../hooks/api';

// A past chat day is only readable through the Journal feed — ChatPanel shows
// the current day and nothing else. So whatever this card drops is gone from
// the app entirely, which is what happened to voice clips and photos: the
// route enriched them, the card rendered `content` alone.

const CONVERSATION = {
  id: 'c1',
  title: 'Yesterday',
  dayKey: '2026-07-01',
  mode: 'chat',
  createdAt: '2026-07-01T10:00:00Z',
  updatedAt: '2026-07-01T10:00:00Z',
  messageCount: 1,
};

const WITH_MESSAGES = {
  ...CONVERSATION,
  messages: [
    {
      id: 'm1',
      conversationId: 'c1',
      role: 'user' as const,
      content: 'What I said out loud',
      metadata: null,
      createdAt: '2026-07-01T10:00:00Z',
      attachments: [
        {
          id: 'a-photo',
          conversationId: 'c1',
          messageId: 'm1',
          url: '/api/chat/attachments/a-photo/file',
          mime: 'image/jpeg',
          kind: 'image',
          description: 'A whiteboard',
          descriptionStatus: 'done',
          position: 0,
          createdAt: '2026-07-01T10:00:00Z',
        },
        {
          id: 'a-clip',
          conversationId: 'c1',
          messageId: 'm1',
          url: '/api/chat/attachments/a-clip/file',
          mime: 'audio/webm',
          kind: 'audio',
          transcript: 'What I said out loud',
          transcriptStatus: 'done',
          position: 1,
          createdAt: '2026-07-01T10:00:01Z',
        },
      ],
    },
  ],
};

vi.mock('../hooks/api', () => ({
  api: {
    journal: {
      list: vi.fn().mockResolvedValue([]),
      search: vi.fn().mockResolvedValue([]),
      mergeCandidates: vi.fn().mockResolvedValue([]),
      attachments: { list: vi.fn() },
      voiceDrafts: { list: vi.fn().mockResolvedValue([]) },
    },
    curatedTags: { list: vi.fn().mockResolvedValue([]) },
    newspapers: { journalIssues: vi.fn().mockResolvedValue([]) },
    study: { journal: vi.fn().mockResolvedValue([]) },
    paper: { journal: vi.fn().mockResolvedValue([]) },
    transcriptions: { list: vi.fn().mockResolvedValue([]) },
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
    chat: {
      journalConversations: vi.fn(),
      getConversation: vi.fn(),
    },
  },
}));

vi.mock('../hooks/useRecorder', () => ({
  useRecorder: () => ({
    status: 'idle',
    canTranscribe: false,
    error: '',
    start: vi.fn(),
    stop: vi.fn(),
  }),
}));

vi.mock('./NewspaperReader', () => ({
  NewspaperReader: () => null,
}));

class FakeEventSource {
  onmessage: unknown = null;
  close() {}
}

function renderJournal() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="journal" onViewChange={() => {}}>
        <Journal />
      </ShortcutProvider>
    </QueryClientProvider>
  );
}

describe('a saved chat day in the journal feed', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', FakeEventSource);
    Element.prototype.scrollIntoView = vi.fn();
    vi.mocked(api.chat.journalConversations).mockResolvedValue([
      CONVERSATION,
    ] as never);
    vi.mocked(api.chat.getConversation).mockResolvedValue(
      WITH_MESSAGES as never
    );
  });

  const expand = async () => {
    const summary = await screen.findByText(/Yesterday/);
    fireEvent.click(summary);
    // jsdom does not toggle <details> from a summary click, and the card
    // fetches on the toggle event.
    const details = summary.closest('details') as HTMLDetailsElement;
    details.open = true;
    fireEvent(details, new Event('toggle'));
    return details;
  };

  it('plays back the voice clip a message was spoken into', async () => {
    const { container } = renderJournal();
    const details = await expand();
    await screen.findByText('What I said out loud');

    const audio = container.querySelector('audio');
    expect(audio?.getAttribute('src')).toBe(
      '/api/chat/attachments/a-clip/file'
    );
    // Nothing is fetched until it is played: a long chat day can hold many.
    expect(audio?.getAttribute('preload')).toBe('none');
    expect(details.contains(audio as Node)).toBe(true);
  });

  it('shows the photos, with the reading as their alt text', async () => {
    renderJournal();
    await expand();
    const img = (await screen.findByAltText(
      'A whiteboard'
    )) as HTMLImageElement;
    expect(img.getAttribute('src')).toBe('/api/chat/attachments/a-photo/file');
  });

  it('opens a photo full-screen when it is tapped', async () => {
    const { container } = renderJournal();
    await expand();
    const img = await screen.findByAltText('A whiteboard');
    fireEvent.click(img);
    // The lightbox is a second copy of the same image, over the feed.
    const shown = container.querySelectorAll(
      'img[src="/api/chat/attachments/a-photo/file"]'
    );
    expect(shown.length).toBe(2);
  });
});
