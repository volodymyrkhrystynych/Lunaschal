import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { documentKey } from '../../lib/writingDocuments';
import { useWritingChapterUpdate } from '../../offline/mutationDefaults';
import { useShortcutScope } from '../../shortcuts/ShortcutProvider';
import {
  CHAPTER_FONT_SIZE_STEP,
  getStoredChapterFontSize,
  getStoredFontSize,
  setStoredChapterFontSize,
} from '../../lib/fontSize';
import { ConflictBanner } from './ConflictBanner';
import { useDocumentConflict } from './useDocumentConflict';

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
  const [saveStatus, setSaveStatus] = useState<'saved' | 'saving' | 'unsaved'>(
    'saved'
  );
  const [fontSize, setFontSize] = useState(getStoredChapterFontSize);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveStatusRef = useRef(saveStatus);
  saveStatusRef.current = saveStatus;

  const adjustFontSize = (delta: number) => {
    setFontSize(px => setStoredChapterFontSize(px + delta));
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

  // Offline-queueable: each save names the revision it was edited from, read
  // when it is sent, so it replays cleanly on reconnect and a device that
  // moved on in the meantime is noticed rather than overwritten.
  const updateChapter = useWritingChapterUpdate({
    onSuccess: reply => {
      if (reply.conflict) {
        setSaveStatus('unsaved');
        conflict.refused();
      } else {
        setSaveStatus('saved');
      }
    },
    onError: () => setSaveStatus('unsaved'),
  });

  const conflict = useDocumentConflict({
    documentKey: documentKey('chapter', chapterId),
    document: chapter,
    isClean: () => saveStatusRef.current === 'saved',
    load: (loaded, text) => {
      setContent(text);
      setTitle(loaded.title);
      setSaveStatus(text === loaded.content ? 'saved' : 'unsaved');
    },
    fetchLatest: () => api.writing.getChapter(chapterId),
    saveText: (text, baseRevision) => {
      setSaveStatus('saving');
      updateChapter.mutate({ chapterId, content: text, baseRevision });
    },
  });

  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, []);

  const handleContentChange = (value: string) => {
    setContent(value);
    setSaveStatus('unsaved');
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    if (conflict.conflict) {
      conflict.holdEdit(value);
      return;
    }
    saveTimerRef.current = setTimeout(() => {
      setSaveStatus('saving');
      updateChapter.mutate({
        chapterId,
        content: value,
        baseRevision: chapter?.revision,
      });
    }, SAVE_DEBOUNCE_MS);
  };

  const handleTitleSave = () => {
    const trimmed = title.trim();
    if (!trimmed) return;
    setEditingTitle(false);
    updateChapter.mutate({
      chapterId,
      title: trimmed,
      baseRevision: chapter?.revision,
    });
  };

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
        Loading…
      </div>
    );
  }

  const statusLabel =
    saveStatus === 'saved'
      ? 'Saved'
      : saveStatus === 'saving'
        ? 'Saving…'
        : 'Unsaved';
  const statusColor =
    saveStatus === 'saved'
      ? 'text-green-500'
      : saveStatus === 'saving'
        ? 'text-yellow-400'
        : 'text-[var(--color-text-muted)]';
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
            onKeyDown={e => {
              if (e.key === 'Enter') handleTitleSave();
              if (e.key === 'Escape') {
                setTitle(chapter?.title ?? '');
                setEditingTitle(false);
              }
            }}
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
          {fontSize !== getStoredFontSize() && <span>{fontSize}px</span>}
          <span>
            {wordCount.toLocaleString()} {wordCount === 1 ? 'word' : 'words'}
          </span>
          <span className={statusColor}>{statusLabel}</span>
        </div>
      </div>

      {conflict.conflict && (
        <ConflictBanner
          onKeepMine={() => void conflict.keepMine(content)}
          onLoadLatest={() => void conflict.loadLatest()}
        />
      )}

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
