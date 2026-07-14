import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { useShortcutScope } from '../../shortcuts/ShortcutProvider';
import { ProjectList } from './ProjectList';
import { WritingNav, type Selection } from './WritingNav';
import { ChapterEditor } from './ChapterEditor';
import { NoteEditor } from './NoteEditor';
import { DiscussionView } from './DiscussionView';

export function Writing() {
  const [selectedProjectId, setSelectedProjectId] = useState<string>('');
  const [selection, setSelection] = useState<Selection>(null);
  const [navVisible, setNavVisible] = useState(true);

  useShortcutScope(1, {
    toggleList: () => setNavVisible((v) => !v),
  });

  const { data: project } = useQuery({
    queryKey: ['writing', 'project', selectedProjectId],
    queryFn: () => api.writing.getProject(selectedProjectId),
    enabled: !!selectedProjectId,
  });

  const handleSelectProject = (id: string) => {
    setSelectedProjectId(id);
    setSelection(null);
  };

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* Left nav: Projects + Chapters/Notes/Discussions stacked */}
      {navVisible && (
        <div data-writing-nav className="w-64 shrink-0 border-r border-white/10 bg-[var(--color-surface)] flex flex-col overflow-hidden">
          <div className={`${selectedProjectId ? 'h-2/5' : 'flex-1'} border-b border-white/10 overflow-hidden flex flex-col`}>
            <ProjectList
              selectedProjectId={selectedProjectId}
              onSelectProject={handleSelectProject}
            />
          </div>
          {selectedProjectId && (
            <div className="flex-1 overflow-hidden flex flex-col">
              <WritingNav
                projectId={selectedProjectId}
                selection={selection}
                onSelect={setSelection}
              />
            </div>
          )}
        </div>
      )}

      {/* Center: chapter editor | note editor | discussion */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {selection?.kind === 'chapter' ? (
          <ChapterEditor chapterId={selection.id} />
        ) : selection?.kind === 'note' ? (
          <NoteEditor noteId={selection.id} />
        ) : selection?.kind === 'discussion' && project ? (
          <DiscussionView
            key={selection.id}
            project={project}
            discussionId={selection.id}
            onNoteCreated={(noteId) => setSelection({ kind: 'note', id: noteId })}
          />
        ) : selectedProjectId ? (
          <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
            Select a chapter, note, or discussion
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
            Select or create a project
          </div>
        )}
      </div>
    </div>
  );
}
