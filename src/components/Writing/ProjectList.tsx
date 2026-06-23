import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type WritingProject } from '../../hooks/api';

interface Props {
  selectedProjectId: string | null;
  onSelectProject: (id: string) => void;
}

export function ProjectList({ selectedProjectId, onSelectProject }: Props) {
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const queryClient = useQueryClient();

  const { data: projects, isLoading } = useQuery({
    queryKey: ['writing', 'projects'],
    queryFn: api.writing.listProjects,
  });

  const createProject = useMutation({
    mutationFn: api.writing.createProject,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['writing', 'projects'] });
      onSelectProject(data.id);
      setCreating(false);
      setNewTitle('');
    },
  });

  const deleteProject = useMutation({
    mutationFn: api.writing.deleteProject,
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: ['writing', 'projects'] });
      if (selectedProjectId === id) onSelectProject('');
    },
  });

  const handleCreate = () => {
    const title = newTitle.trim();
    if (!title) return;
    createProject.mutate({ title });
  };

  return (
    <div className="flex flex-col h-full">
      <div className="p-3 border-b border-white/10 shrink-0">
        <div className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider mb-2">Projects</div>
        {creating ? (
          <div className="flex gap-1">
            <input
              autoFocus
              value={newTitle}
              onChange={e => setNewTitle(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleCreate(); if (e.key === 'Escape') { setCreating(false); setNewTitle(''); } }}
              placeholder="Project title…"
              className="flex-1 px-2 py-1 text-sm rounded bg-white/5 border border-white/20 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
            />
            <button
              onClick={handleCreate}
              disabled={createProject.isPending}
              className="px-2 py-1 text-sm rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-50"
            >
              Add
            </button>
          </div>
        ) : (
          <button
            onClick={() => setCreating(true)}
            className="w-full py-1.5 px-2 text-sm rounded border border-white/20 text-[var(--color-text)] hover:bg-white/10 transition-colors"
          >
            + New Project
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {isLoading && (
          <div className="text-sm text-[var(--color-text-muted)] px-2 py-2">Loading…</div>
        )}
        {projects?.map((project: WritingProject) => (
          <div
            key={project.id}
            className={`group flex items-center justify-between px-2 py-2 rounded cursor-pointer transition-colors ${
              selectedProjectId === project.id
                ? 'bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                : 'text-[var(--color-text)] hover:bg-white/10'
            }`}
            onClick={() => onSelectProject(project.id)}
          >
            <span className="text-sm truncate flex-1">{project.title}</span>
            <button
              onClick={e => { e.stopPropagation(); if (confirm(`Delete "${project.title}" and all its content?`)) deleteProject.mutate(project.id); }}
              className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-white/20 text-[var(--color-text-muted)] hover:text-red-400 transition-all"
              title="Delete project"
            >
              ✕
            </button>
          </div>
        ))}
        {!isLoading && (!projects || projects.length === 0) && (
          <div className="text-sm text-[var(--color-text-muted)] px-2 py-2">No projects yet</div>
        )}
      </div>
    </div>
  );
}
