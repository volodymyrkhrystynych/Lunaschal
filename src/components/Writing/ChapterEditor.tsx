import { useEffect, useRef, useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { useShortcutScope } from '../../shortcuts/ShortcutProvider';
import {
  CHAPTER_FONT_SIZE_DEFAULT, CHAPTER_FONT_SIZE_STEP,
  getStoredChapterFontSize, setStoredChapterFontSize,
} from '../../lib/fontSize';

interface Props {
  chapterId: string;
}

const SAVE_DEBOUNCE_MS = 1500;

function countWords(text: string): number {
  return text.trim() ? text.trim().split(/\s+/).length : 0;
}

export function ChapterEditor({ chapterId }: Props) {
  const [content, setContent] = useState('');
  const [title, setTitle] = useState('');
  const [editingTitle, setEditingTitle] = useState(false);
  const [saveStatus, setSaveStatus] = useState<'saved' | 'saving' | 'unsaved'>('saved');
  const [fontSize, setFontSize] = useState(getStoredChapterFontSize);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadedChapterRef = useRef<string | null>(null);

  const adjustFontSize = (delta: number) => {
    setFontSize((px) => setStoredChapterFontSize(px + delta));
  };

  useShortcutScope(1, {
    fontUp: () => adjustFontSize(CHAPTER_FONT_SIZE_STEP),
    fontDown: () => adjustFontSize(-CHAPTER_FONT_SIZE_STEP),
  });

  const { data: chapter, isLoading } = useQuery({
    queryKey: ['writing', 'chapter', chapterId],
    queryFn: () => api.writing.getChapter(chapterId),
    enabled: !!chapterId,
  });

  const updateChapter = useMutation({
    mutationFn: (data: { title?: string; content?: string }) =>
      api.writing.updateChapter(chapterId, data),
    onSuccess: () => setSaveStatus('saved'),
  });

  useEffect(() => {
    if (chapter && loadedChapterRef.current !== chapterId) {
      setContent(chapter.content);
      setTitle(chapter.title);
      setSaveStatus('saved');
      loadedChapterRef.current = chapterId;
    }
  }, [chapter, chapterId]);

  useEffect(() => {
    return () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current); };
  }, []);

  const handleContentChange = (value: string) => {
    setContent(value);
    setSaveStatus('unsaved');
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      setSaveStatus('saving');
      updateChapter.mutate({ content: value });
    }, SAVE_DEBOUNCE_MS);
  };

  const handleTitleSave = () => {
    const trimmed = title.trim();
    if (!trimmed) return;
    setEditingTitle(false);
    updateChapter.mutate({ title: trimmed });
  };

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">Loading…</div>
    );
  }

  const statusLabel = saveStatus === 'saved' ? 'Saved' : saveStatus === 'saving' ? 'Saving…' : 'Unsaved';
  const statusColor = saveStatus === 'saved' ? 'text-green-500' : saveStatus === 'saving' ? 'text-yellow-400' : 'text-[var(--color-text-muted)]';
  const wordCount = countWords(content);

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 border-b border-white/10 bg-[var(--color-surface)] shrink-0 gap-3">
        {editingTitle ? (
          <input
            autoFocus
            value={title}
            onChange={e => setTitle(e.target.value)}
            onBlur={handleTitleSave}
            onKeyDown={e => { if (e.key === 'Enter') handleTitleSave(); if (e.key === 'Escape') { setTitle(chapter?.title ?? ''); setEditingTitle(false); } }}
            className="flex-1 bg-transparent text-sm font-medium text-[var(--color-text)] border-b border-[var(--color-primary)] focus:outline-none"
          />
        ) : (
          <button
            onClick={() => setEditingTitle(true)}
            className="flex-1 text-left text-sm font-medium text-[var(--color-text)] hover:text-[var(--color-primary)] transition-colors truncate"
            title="Click to rename"
          >
            {chapter?.title}
          </button>
        )}
        <div className="flex items-center gap-3 shrink-0 text-xs text-[var(--color-text-muted)]">
          {fontSize !== CHAPTER_FONT_SIZE_DEFAULT && <span>{fontSize}px</span>}
          <span>{wordCount.toLocaleString()} {wordCount === 1 ? 'word' : 'words'}</span>
          <span className={statusColor}>{statusLabel}</span>
        </div>
      </div>

      <textarea
        data-chapter-editor
        value={content}
        onChange={e => handleContentChange(e.target.value)}
        placeholder="Start writing…"
        className="flex-1 resize-none bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] p-6 focus:outline-none leading-relaxed font-serif"
        style={{ fontSize: `${fontSize}px` }}
        spellCheck
      />
    </div>
  );
}
