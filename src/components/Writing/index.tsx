import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { ProjectList } from './ProjectList';
import { ChapterList } from './ChapterList';
import { ChapterEditor } from './ChapterEditor';
import { WritingChat } from './WritingChat';

export function Writing() {
  const [selectedProjectId, setSelectedProjectId] = useState<string>('');
  const [selectedChapterId, setSelectedChapterId] = useState<string>('');

  const { data: project } = useQuery({
    queryKey: ['writing', 'project', selectedProjectId],
    queryFn: () => api.writing.getProject(selectedProjectId),
    enabled: !!selectedProjectId,
  });

  const handleSelectProject = (id: string) => {
    setSelectedProjectId(id);
    setSelectedChapterId('');
  };

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* Left nav: Projects + Chapters stacked */}
      <div className="w-56 shrink-0 border-r border-white/10 bg-[var(--color-surface)] flex flex-col overflow-hidden">
        <div className={`${selectedProjectId ? 'h-1/2' : 'flex-1'} border-b border-white/10 overflow-hidden flex flex-col`}>
          <ProjectList
            selectedProjectId={selectedProjectId}
            onSelectProject={handleSelectProject}
          />
        </div>
        {selectedProjectId && (
          <div className="flex-1 overflow-hidden flex flex-col">
            <ChapterList
              projectId={selectedProjectId}
              selectedChapterId={selectedChapterId}
              onSelectChapter={setSelectedChapterId}
            />
          </div>
        )}
      </div>

      {/* Center: Chapter editor */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {selectedChapterId ? (
          <ChapterEditor chapterId={selectedChapterId} />
        ) : selectedProjectId ? (
          <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
            Select a chapter to start writing
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
            Select or create a project
          </div>
        )}
      </div>

      {/* Right: Chat sidebar */}
      <div className="w-80 shrink-0 flex flex-col overflow-hidden">
        {project ? (
          <WritingChat project={project} />
        ) : (
          <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)] border-l border-white/10 text-sm p-4 text-center">
            Select a project to chat about your story
          </div>
        )}
      </div>
    </div>
  );
}
