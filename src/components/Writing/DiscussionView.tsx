import { useEffect, useRef, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type WritingProject, type WritingNote } from '../../hooks/api';
import { MessageMarkdown } from '../MessageMarkdown';
import { DOC_TYPE_LABELS, type DocType } from './WritingNav';

interface Props {
  project: WritingProject;
  discussionId: string;
  onNoteCreated?: (noteId: string) => void;
}

export function DiscussionView({
  project,
  discussionId,
  onNoteCreated,
}: Props) {
  const [input, setInput] = useState('');
  const [streamingContent, setStreamingContent] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [selectedNoteIds, setSelectedNoteIds] = useState<Set<string>>(
    new Set()
  );
  const [showContextPanel, setShowContextPanel] = useState(false);
  const [title, setTitle] = useState('');
  const [editingTitle, setEditingTitle] = useState(false);
  const [savedNote, setSavedNote] = useState<WritingNote | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  const { data: conversation } = useQuery({
    queryKey: ['chat', 'conversation', discussionId],
    queryFn: () => api.chat.getConversation(discussionId),
    enabled: !!discussionId,
  });

  const { data: notes } = useQuery({
    queryKey: ['writing', 'notes', project.id],
    queryFn: () => api.writing.listNotes(project.id),
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });

  const addMessage = useMutation({
    mutationFn: ({ role, content }: { role: string; content: string }) =>
      api.chat.addMessage(discussionId, { role, content }),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ['chat', 'conversation', discussionId],
      }),
  });

  const updateTitle = useMutation({
    mutationFn: (newTitle: string) =>
      api.chat.updateTitle(discussionId, newTitle),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ['writing', 'discussions', project.id],
      });
      queryClient.invalidateQueries({
        queryKey: ['chat', 'conversation', discussionId],
      });
    },
  });

  const summarize = useMutation({
    mutationFn: () => api.writing.summarizeDiscussion(discussionId),
    onSuccess: note => {
      queryClient.invalidateQueries({
        queryKey: ['writing', 'notes', project.id],
      });
      setSavedNote(note);
    },
  });

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView?.({ behavior: 'smooth' });
  }, [conversation?.messages, streamingContent]);

  const toggleNote = (noteId: string) => {
    setSelectedNoteIds(prev => {
      const next = new Set(prev);
      if (next.has(noteId)) next.delete(noteId);
      else next.add(noteId);
      return next;
    });
  };

  const buildSystemPrompt = (checkedNotes: WritingNote[]): string => {
    let prompt = `You are a creative writing partner helping the author develop the story "${project.title}".`;
    if (project.description)
      prompt += `\n\nStory description: ${project.description}`;

    if (checkedNotes.length > 0) {
      prompt += '\n\nNotes provided by the author:';
      checkedNotes.forEach(note => {
        prompt += `\n\n--- ${note.docType}: ${note.title} ---\n${note.content}`;
      });
    }

    prompt += `

This is a brainstorming discussion. Help the author generate and refine ideas — plot, characters, worldbuilding, themes, and structure. Offer concrete suggestions with alternatives, point out weaknesses, contradictions, and clichés honestly instead of praising everything, and ask sharp questions when an idea is underdeveloped. Build on the author's ideas rather than replacing them; when the author makes a decision, treat it as canon for the rest of the discussion. Keep replies conversational and focused. This discussion may later be distilled into reference notes, so favor substance over filler.`;
    return prompt;
  };

  const sendMessage = async () => {
    if (!input.trim() || isStreaming) return;

    const userMessage = input.trim();
    setInput('');

    await addMessage.mutateAsync({ role: 'user', content: userMessage });

    const messages = [
      ...(conversation?.messages || []).map(m => ({
        role: m.role,
        content: m.content,
      })),
      { role: 'user' as const, content: userMessage },
    ];

    setIsStreaming(true);
    setStreamingContent('');

    try {
      const checkedNotes = await Promise.all(
        [...selectedNoteIds].map(id => api.writing.getNote(id))
      );

      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          messages,
          systemPrompt: buildSystemPrompt(checkedNotes),
        }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || 'Failed to get response');
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');

      const decoder = new TextDecoder();
      let fullContent = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const lines = decoder.decode(value).split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6);
            if (data === '[DONE]') continue;
            try {
              const parsed = JSON.parse(data);
              if (parsed.content) {
                fullContent += parsed.content;
                setStreamingContent(fullContent);
              }
              if (parsed.error) throw new Error(parsed.error);
            } catch {
              /* ignore parse errors */
            }
          }
        }
      }

      await addMessage.mutateAsync({ role: 'assistant', content: fullContent });
    } catch (error) {
      setStreamingContent(
        `Error: ${error instanceof Error ? error.message : 'Failed to get response'}`
      );
    } finally {
      setIsStreaming(false);
      setStreamingContent('');
    }
  };

  const handleTitleSave = () => {
    const trimmed = title.trim();
    setEditingTitle(false);
    if (trimmed && trimmed !== conversation?.title) updateTitle.mutate(trimmed);
  };

  const aiConfigured = !!settings?.ollamaUrl;
  const messages = (conversation?.messages || []).filter(
    m => m.role !== 'system'
  );

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header: title + summarize */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-white/10 bg-[var(--color-surface)] shrink-0 gap-3">
        {editingTitle ? (
          <input
            autoFocus
            value={title}
            onChange={e => setTitle(e.target.value)}
            onBlur={handleTitleSave}
            onKeyDown={e => {
              if (e.key === 'Enter') handleTitleSave();
              if (e.key === 'Escape') setEditingTitle(false);
            }}
            className="flex-1 bg-transparent text-sm font-medium text-[var(--color-text)] border-b border-[var(--color-primary)] focus:outline-none"
          />
        ) : (
          <button
            onClick={() => {
              setTitle(conversation?.title ?? '');
              setEditingTitle(true);
            }}
            className="flex-1 text-left text-sm font-medium text-[var(--color-text)] hover:text-[var(--color-primary)] transition-colors truncate"
            title="Click to rename"
          >
            {conversation?.title || 'Untitled discussion'}
          </button>
        )}
        <button
          onClick={() => {
            setSavedNote(null);
            summarize.mutate();
          }}
          disabled={summarize.isPending || messages.length === 0}
          className="text-sm px-3 py-1 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-50 transition-colors shrink-0"
          title="Distill this discussion into a note"
        >
          {summarize.isPending ? 'Summarizing…' : 'Summarize'}
        </button>
      </div>

      {/* Summarize result / error banner */}
      {savedNote && (
        <div className="flex items-center justify-between gap-2 px-4 py-2 text-sm bg-green-500/10 text-green-400 border-b border-white/10 shrink-0">
          <span className="truncate">
            Summary saved to Notes: “{savedNote.title}”
          </span>
          <div className="flex items-center gap-2 shrink-0">
            {onNoteCreated && (
              <button
                onClick={() => onNoteCreated(savedNote.id)}
                className="underline hover:no-underline"
              >
                Open note
              </button>
            )}
            <button
              onClick={() => setSavedNote(null)}
              className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              ✕
            </button>
          </div>
        </div>
      )}
      {summarize.isError && !savedNote && (
        <div className="px-4 py-2 text-sm bg-red-500/10 text-red-400 border-b border-white/10 shrink-0">
          {summarize.error instanceof Error
            ? summarize.error.message
            : 'Summarization failed'}
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
        {messages.length === 0 && !isStreaming && (
          <div className="text-sm text-[var(--color-text-muted)] text-center mt-8">
            Brainstorm ideas, characters, and plots — then hit Summarize to save
            the takeaways as a note
          </div>
        )}
        {messages.map(msg => (
          <div
            key={msg.id}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[70%] rounded-lg px-3 py-2 text-sm leading-relaxed ${
                msg.role === 'user'
                  ? 'bg-[var(--color-primary)]/20 text-[var(--color-text)] whitespace-pre-wrap'
                  : 'bg-white/5 text-[var(--color-text)]'
              }`}
            >
              {msg.role === 'user' ? (
                msg.content
              ) : (
                <MessageMarkdown content={msg.content} />
              )}
            </div>
          </div>
        ))}
        {isStreaming && streamingContent && (
          <div className="flex justify-start">
            <div className="max-w-[70%] rounded-lg px-3 py-2 text-sm leading-relaxed bg-white/5 text-[var(--color-text)]">
              <MessageMarkdown content={streamingContent} />
              <span className="inline-block w-1 h-3 bg-[var(--color-primary)] animate-pulse ml-0.5" />
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Context notes panel */}
      <div className="border-t border-white/10 shrink-0">
        <button
          onClick={() => setShowContextPanel(!showContextPanel)}
          className="w-full px-4 py-2 flex items-center justify-between text-xs text-[var(--color-text-muted)] hover:bg-white/5 transition-colors"
        >
          <span>
            Context notes
            {selectedNoteIds.size > 0
              ? ` (${selectedNoteIds.size} selected)`
              : ''}
          </span>
          <span>{showContextPanel ? '▲' : '▼'}</span>
        </button>

        {showContextPanel && (
          <div className="px-4 pb-3 flex flex-col gap-1">
            {notes?.map(note => (
              <div key={note.id} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id={`ctx-${note.id}`}
                  checked={selectedNoteIds.has(note.id)}
                  onChange={() => toggleNote(note.id)}
                  className="accent-[var(--color-primary)]"
                />
                <label
                  htmlFor={`ctx-${note.id}`}
                  className="text-xs text-[var(--color-text)] flex-1 cursor-pointer truncate"
                >
                  <span className="text-[var(--color-text-muted)] mr-1">
                    [{DOC_TYPE_LABELS[note.docType as DocType] ?? note.docType}]
                  </span>
                  {note.title}
                </label>
              </div>
            ))}
            {(!notes || notes.length === 0) && (
              <div className="text-xs text-[var(--color-text-muted)]">
                No notes yet — checked notes are sent to the AI as context
              </div>
            )}
          </div>
        )}
      </div>

      {/* Input area */}
      <div className="border-t border-white/10 p-4 shrink-0">
        {!aiConfigured && (
          <div className="text-xs text-yellow-400 mb-2">
            Configure AI provider in Settings to chat
          </div>
        )}
        <div className="flex gap-2">
          <textarea
            data-discussion-input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
              }
            }}
            placeholder="Discuss your story… (Enter to send)"
            disabled={isStreaming || !aiConfigured}
            rows={2}
            className="flex-1 resize-none rounded bg-white/5 border border-white/20 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)] disabled:opacity-50"
          />
          <button
            onClick={sendMessage}
            disabled={!input.trim() || isStreaming || !aiConfigured}
            className="px-3 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-50 transition-colors self-end py-2 text-sm"
          >
            {isStreaming ? '…' : '→'}
          </button>
        </div>
      </div>
    </div>
  );
}
