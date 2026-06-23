import { useEffect, useRef, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type WritingProject, type WritingContextDoc } from '../../hooks/api';
import { ContextDocCheckboxList, ContextDocEditor } from './ContextDocPanel';

interface Props {
  project: WritingProject;
}

type EditingDoc = { id: string } | { id: null };

export function WritingChat({ project }: Props) {
  const [input, setInput] = useState('');
  const [streamingContent, setStreamingContent] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [selectedConvId, setSelectedConvId] = useState<string | null>(null);
  const [selectedDocIds, setSelectedDocIds] = useState<Set<string>>(new Set());
  const [docContents, setDocContents] = useState<Map<string, WritingContextDoc>>(new Map());
  const [showContextPanel, setShowContextPanel] = useState(false);
  const [editingDoc, setEditingDoc] = useState<EditingDoc | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  const { data: conversations } = useQuery({
    queryKey: ['writing', 'conversations', project.id],
    queryFn: () => api.writing.listProjectConversations(project.id),
  });

  const { data: conversation } = useQuery({
    queryKey: ['chat', 'conversation', selectedConvId],
    queryFn: () => api.chat.getConversation(selectedConvId!),
    enabled: !!selectedConvId,
  });

  const { data: allDocs } = useQuery({
    queryKey: ['writing', 'context-docs', project.id],
    queryFn: () => api.writing.listContextDocs(project.id),
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });

  const createConversation = useMutation({
    mutationFn: () => api.writing.createProjectConversation(project.id, { title: 'Story Chat' }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['writing', 'conversations', project.id] });
      setSelectedConvId(data.id);
    },
  });

  const addMessage = useMutation({
    mutationFn: ({ convId, role, content }: { convId: string; role: string; content: string }) =>
      api.chat.addMessage(convId, { role, content }),
    onSuccess: (_, vars) =>
      queryClient.invalidateQueries({ queryKey: ['chat', 'conversation', vars.convId] }),
  });

  // Eagerly load full content for all context docs
  useEffect(() => {
    if (!allDocs) return;
    const newMap = new Map(docContents);
    let changed = false;
    allDocs.forEach(doc => {
      if (!newMap.has(doc.id)) {
        api.writing.getContextDoc(doc.id).then(full => {
          setDocContents(prev => new Map(prev).set(full.id, full));
        }).catch(() => {});
        changed = true;
      }
    });
    // Remove docs that no longer exist
    newMap.forEach((_, id) => {
      if (!allDocs.find(d => d.id === id)) { newMap.delete(id); changed = true; }
    });
    if (changed) setDocContents(newMap);
  }, [allDocs]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [conversation?.messages, streamingContent]);

  const toggleDoc = (docId: string) => {
    setSelectedDocIds(prev => {
      const next = new Set(prev);
      if (next.has(docId)) next.delete(docId); else next.add(docId);
      return next;
    });
  };

  const buildSystemPrompt = (): string => {
    let prompt = `You are a creative writing assistant for the story "${project.title}".`;
    if (project.description) prompt += `\n\nStory description: ${project.description}`;

    const checkedDocs = [...selectedDocIds]
      .map(id => docContents.get(id))
      .filter((d): d is WritingContextDoc => !!d);

    if (checkedDocs.length > 0) {
      prompt += '\n\nContext documents provided by the author:';
      checkedDocs.forEach(doc => {
        prompt += `\n\n--- ${doc.docType}: ${doc.title} ---\n${doc.content}`;
      });
    }

    prompt += '\n\nFocus on the story, characters, plot, and world. Do not offer to save to journal or calendar.';
    return prompt;
  };

  const sendMessage = async () => {
    if (!input.trim() || isStreaming) return;

    const userMessage = input.trim();
    setInput('');

    let convId = selectedConvId;
    if (!convId) {
      const result = await createConversation.mutateAsync();
      convId = result.id;
    }

    await addMessage.mutateAsync({ convId, role: 'user', content: userMessage });

    const messages = [
      ...(conversation?.messages || []).map(m => ({ role: m.role, content: m.content })),
      { role: 'user' as const, content: userMessage },
    ];

    setIsStreaming(true);
    setStreamingContent('');

    try {
      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ messages, systemPrompt: buildSystemPrompt() }),
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
              if (parsed.content) { fullContent += parsed.content; setStreamingContent(fullContent); }
              if (parsed.error) throw new Error(parsed.error);
            } catch { /* ignore parse errors */ }
          }
        }
      }

      await addMessage.mutateAsync({ convId: convId!, role: 'assistant', content: fullContent });
    } catch (error) {
      setStreamingContent(`Error: ${error instanceof Error ? error.message : 'Failed to get response'}`);
    } finally {
      setIsStreaming(false);
      setStreamingContent('');
    }
  };

  const aiConfigured = !!(settings?.hasOpenaiKey || settings?.hasGoogleKey || settings?.ollamaUrl);
  const messages = conversation?.messages || [];

  return (
    <div className="flex flex-col h-full border-l border-white/10 bg-[var(--color-surface)]">
      {/* Conversation selector */}
      <div className="px-3 py-2 border-b border-white/10 flex items-center gap-2 shrink-0">
        <select
          value={selectedConvId ?? ''}
          onChange={e => setSelectedConvId(e.target.value || null)}
          className="flex-1 text-xs rounded bg-white/5 border border-white/20 text-[var(--color-text)] px-2 py-1 focus:outline-none"
        >
          <option value="">Select conversation…</option>
          {conversations?.map(c => (
            <option key={c.id} value={c.id}>{c.title || 'Story Chat'}</option>
          ))}
        </select>
        <button
          onClick={() => createConversation.mutate()}
          disabled={createConversation.isPending}
          className="text-xs px-2 py-1 rounded border border-white/20 text-[var(--color-text)] hover:bg-white/10 transition-colors disabled:opacity-50 shrink-0"
          title="New conversation"
        >
          +
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-3">
        {!selectedConvId && (
          <div className="text-sm text-[var(--color-text-muted)] text-center mt-8">
            Start a new conversation or select one above
          </div>
        )}
        {messages.filter(m => m.role !== 'system').map(msg => (
          <div
            key={msg.id}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[85%] rounded-lg px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap ${
                msg.role === 'user'
                  ? 'bg-[var(--color-primary)]/20 text-[var(--color-text)]'
                  : 'bg-white/5 text-[var(--color-text)]'
              }`}
            >
              {msg.content}
            </div>
          </div>
        ))}
        {isStreaming && streamingContent && (
          <div className="flex justify-start">
            <div className="max-w-[85%] rounded-lg px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap bg-white/5 text-[var(--color-text)]">
              {streamingContent}
              <span className="inline-block w-1 h-3 bg-[var(--color-primary)] animate-pulse ml-0.5" />
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Context docs panel */}
      <div className="border-t border-white/10 shrink-0">
        <button
          onClick={() => setShowContextPanel(!showContextPanel)}
          className="w-full px-3 py-2 flex items-center justify-between text-xs text-[var(--color-text-muted)] hover:bg-white/5 transition-colors"
        >
          <span>
            Context{selectedDocIds.size > 0 ? ` (${selectedDocIds.size} selected)` : ''}
          </span>
          <span>{showContextPanel ? '▲' : '▼'}</span>
        </button>

        {showContextPanel && (
          <div className="px-3 pb-3 flex flex-col gap-2">
            {editingDoc !== null ? (
              <ContextDocEditor
                projectId={project.id}
                docId={editingDoc.id}
                onClose={() => {
                  setEditingDoc(null);
                  // Reload doc contents after edit
                  queryClient.invalidateQueries({ queryKey: ['writing', 'context-docs', project.id] });
                  if (editingDoc.id) queryClient.invalidateQueries({ queryKey: ['writing', 'context-doc', editingDoc.id] });
                }}
              />
            ) : (
              <ContextDocCheckboxList
                projectId={project.id}
                selectedIds={selectedDocIds}
                onToggle={toggleDoc}
                onEdit={id => setEditingDoc({ id })}
                onAdd={() => setEditingDoc({ id: null })}
              />
            )}
          </div>
        )}
      </div>

      {/* Input area */}
      <div className="border-t border-white/10 p-3 shrink-0">
        {!aiConfigured && (
          <div className="text-xs text-yellow-400 mb-2">Configure AI provider in Settings to chat</div>
        )}
        <div className="flex gap-2">
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } }}
            placeholder="Ask about your story… (Enter to send)"
            disabled={isStreaming || !aiConfigured || !selectedConvId}
            rows={2}
            className="flex-1 resize-none rounded bg-white/5 border border-white/20 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)] disabled:opacity-50"
          />
          <button
            onClick={sendMessage}
            disabled={!input.trim() || isStreaming || !aiConfigured || !selectedConvId}
            className="px-3 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-50 transition-colors self-end py-2 text-sm"
          >
            {isStreaming ? '…' : '→'}
          </button>
        </div>
      </div>
    </div>
  );
}
