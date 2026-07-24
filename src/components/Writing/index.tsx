import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { useShortcutScope } from '../../shortcuts/ShortcutProvider';
import { ProjectList } from './ProjectList';
import { WritingNav, type Selection } from './WritingNav';
import { ChapterEditor } from './ChapterEditor';
import { NoteEditor } from './NoteEditor';
import { DiscussionView } from './DiscussionView';
import { useMasterDetail } from '@/hooks/useMasterDetail';
import { MasterDetailBack } from '@/components/MasterDetailBack';

export function Writing() {
  const [selectedProjectId, setSelectedProjectId] = useState<string>('');
  const [selection, setSelection] = useState<Selection>(null);
  const [navVisible, setNavVisible] = useState(true);
  const { isMobile, showList, showDetail, openDetail, openList } =
    useMasterDetail();

  useShortcutScope(1, {
    toggleList: () => setNavVisible(v => !v),
  });

  // Desktop uses the power-user `navVisible` collapse; mobile shows exactly one
  // of nav (list) / editor (detail). Keep the two concepts separate.
  const navShown = isMobile ? showList : navVisible;
  const centerShown = isMobile ? showDetail : true;

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
      {navShown && (
        <div
          data-writing-nav
          className={`${isMobile ? 'w-full' : 'w-64 shrink-0'} border-r border-white/10 bg-[var(--color-surface)] flex flex-col overflow-hidden`}
        >
          <div
            className={`${selectedProjectId ? 'h-2/5' : 'flex-1'} border-b border-white/10 overflow-hidden flex flex-col`}
          >
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
                onSelect={sel => {
                  setSelection(sel);
                  openDetail();
                }}
              />
            </div>
          )}
        </div>
      )}

      {/* Center: chapter editor | note editor | discussion */}
      {centerShown && (
        <div className="flex-1 flex flex-col overflow-hidden">
          <MasterDetailBack onClick={openList} label="Writing" />
          {selection?.kind === 'chapter' ? (
            <ChapterEditor chapterId={selection.id} />
          ) : selection?.kind === 'note' ? (
            <NoteEditor noteId={selection.id} />
          ) : selection?.kind === 'discussion' && project ? (
            <DiscussionView
              key={selection.id}
              project={project}
              discussionId={selection.id}
              onNoteCreated={noteId =>
                setSelection({ kind: 'note', id: noteId })
              }
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
      )}
    </div>
  );
}
