// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ShortcutProvider } from '../../shortcuts/ShortcutProvider';
import { api, type WritingProject } from '../../hooks/api';
import { DiscussionView } from './DiscussionView';

vi.mock('../../hooks/api', () => ({
  api: {
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
    writing: {
      listNotes: vi.fn().mockResolvedValue([]),
      getNote: vi.fn(),
      summarizeDiscussion: vi.fn(),
    },
    chat: {
      getConversation: vi.fn().mockResolvedValue({
        id: 'd1',
        title: 'Plot talk',
        messages: [
          { id: 'm1', conversationId: 'd1', role: 'user', content: 'What if?', metadata: null, createdAt: '' },
          { id: 'm2', conversationId: 'd1', role: 'assistant', content: 'Then this.', metadata: null, createdAt: '' },
        ],
      }),
      updateTitle: vi.fn(),
      deleteConversation: vi.fn(),
      addMessage: vi.fn(),
    },
  },
}));

const project: WritingProject = {
  id: 'p1', title: 'My Story', description: null, createdAt: '', updatedAt: '',
};

beforeEach(() => {
  vi.mocked(api.writing.summarizeDiscussion).mockReset();
});

function renderWithProviders(children: ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="writing" onViewChange={() => {}}>
        {children}
      </ShortcutProvider>
    </QueryClientProvider>,
  );
}

describe('assistant markdown rendering', () => {
  it('renders assistant markdown as HTML but keeps user messages literal', async () => {
    vi.mocked(api.chat.getConversation).mockResolvedValueOnce({
      id: 'd3',
      title: 'Markdown talk',
      messages: [
        { id: 'm1', conversationId: 'd3', role: 'user', content: 'use **plain** please', metadata: null, createdAt: '' },
        { id: 'm2', conversationId: 'd3', role: 'assistant', content: 'A **bold** idea', metadata: null, createdAt: '' },
      ],
      createdAt: '', updatedAt: '',
    });
    renderWithProviders(<DiscussionView project={project} discussionId="d3" />);

    const bold = await screen.findByText('bold');
    expect(bold.tagName).toBe('STRONG');
    // User text is not interpreted as markdown
    expect(screen.getByText('use **plain** please')).not.toBeNull();
  });
});

describe('discussion summarize', () => {
  it('saves a summary note and offers to open it', async () => {
    vi.mocked(api.writing.summarizeDiscussion).mockResolvedValue({
      id: 'n9', projectId: 'p1', title: 'Villain twist decision', content: '- twist', docType: 'note', createdAt: '', updatedAt: '',
    });
    const onNoteCreated = vi.fn();
    renderWithProviders(<DiscussionView project={project} discussionId="d1" onNoteCreated={onNoteCreated} />);

    await screen.findByText('What if?'); // wait for the transcript so the button enables
    fireEvent.click(screen.getByText('Summarize'));

    expect(await screen.findByText(/Summary saved to Notes/)).not.toBeNull();
    expect(api.writing.summarizeDiscussion).toHaveBeenCalledWith('d1');

    fireEvent.click(screen.getByText('Open note'));
    expect(onNoteCreated).toHaveBeenCalledWith('n9');
  });

  it('shows the server error when summarization fails', async () => {
    vi.mocked(api.writing.summarizeDiscussion).mockRejectedValue(new Error('AI provider not configured'));
    renderWithProviders(<DiscussionView project={project} discussionId="d1" />);

    await screen.findByText('What if?');
    fireEvent.click(screen.getByText('Summarize'));

    expect(await screen.findByText('AI provider not configured')).not.toBeNull();
    expect(screen.queryByText(/Summary saved to Notes/)).toBeNull();
  });

  it('disables the button while the discussion has no messages', async () => {
    vi.mocked(api.chat.getConversation).mockResolvedValueOnce({
      id: 'd2', title: 'Empty', messages: [], createdAt: '', updatedAt: '',
    });
    renderWithProviders(<DiscussionView project={project} discussionId="d2" />);

    const button = await screen.findByText('Summarize');
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });
});
